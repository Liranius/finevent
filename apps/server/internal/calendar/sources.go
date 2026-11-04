package calendar

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"golang.org/x/net/html"
)

const nbsURL = "https://www.stats.gov.cn/xxgk/sjfb/fbrcb/"

// Annual schedules can take longer to download than small news responses.
// Keep this deadline separate from the news collector and retain the 2 MiB cap.
const RequestTimeout = time.Minute

func NewHTTPClient() *http.Client {
	return &http.Client{Timeout: RequestTimeout, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
}

func fetchError(stage string, err error) error {
	var timeout net.Error
	if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &timeout) && timeout.Timeout()) {
		return errors.New(stage + "超时")
	}
	return errors.New(stage + "失败")
}

func Sources() []Source {
	return []Source{
		{ID: "nbs", Name: "国家统计局", URL: nbsURL},
		{ID: "fomc", Name: "美联储 FOMC", URL: "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"},
		{ID: "bls", Name: "美国 BLS", URL: "https://www.bls.gov/schedule/news_release/bls.ics"},
		{ID: "bea", Name: "美国 BEA", URL: "https://www.bea.gov/news/schedule"},
		{ID: "nyfed", Name: "纽约联储 · BLS 补充", URL: nyfedURL, Mode: "supplement", Note: nyfedNote},
	}
}
func Fetch(ctx context.Context, client *http.Client, rawURL string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, RequestTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", rawURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "finevent/0.2 (personal financial calendar)")
	r, err := client.Do(req)
	if err != nil {
		return "", fetchError("官方来源请求", err)
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return "", fmt.Errorf("官方来源返回 HTTP %d", r.StatusCode)
	}
	b, err := io.ReadAll(io.LimitReader(r.Body, 2*1024*1024+1))
	if err != nil {
		return "", fetchError("读取官方日程", err)
	}
	if len(b) > 2*1024*1024 {
		return "", errors.New("日程文件超过大小限制")
	}
	return string(b), nil
}
func (s *Store) SyncDue(ctx context.Context, client *http.Client) error {
	sources, err := s.Sources()
	if err != nil {
		return err
	}
	for _, source := range sources {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if time.Now().Unix()-source.LastAttempt < 6*3600 {
			retry, err := s.claimRolloverRetry(source)
			if err != nil {
				return err
			}
			if !retry {
				continue
			}
		}
		body, fetchErr := Fetch(ctx, client, source.URL)
		var events []Event
		if fetchErr == nil {
			if source.ID == "nbs" {
				var links []string
				links, fetchErr = nbsLinks(body, time.Now().Year())
				for _, link := range links {
					var text string
					text, fetchErr = Fetch(ctx, client, link)
					if fetchErr != nil {
						break
					}
					var annual []Event
					annual, fetchErr = Parse("nbs", link, text, time.Now().Year())
					if fetchErr != nil {
						break
					}
					events = append(events, annual...)
				}
			} else if source.ID == "nyfed" {
				events, fetchErr = fetchNYFed(ctx, client, body, time.Now())
			} else {
				events, fetchErr = Parse(source.ID, source.URL, body, time.Now().Year())
			}
		}
		if err = s.Import(source.ID, events, time.Now().Unix(), fetchErr); err != nil {
			if e := s.Import(source.ID, nil, time.Now().Unix(), errors.New("日程校验或保存失败，保留上次数据")); e != nil {
				return e
			}
		}
	}
	return nil
}

func nbsLinks(body string, year int) ([]string, error) {
	root, err := html.Parse(strings.NewReader(body))
	if err != nil {
		return nil, err
	}
	base, _ := url.Parse(nbsURL)
	found := map[int]string{}
	walk(root, func(n *html.Node) {
		if n.Data != "a" {
			return
		}
		title := content(n) + attr(n, "title")
		if !strings.Contains(title, "主要统计信息发布日程表") {
			return
		}
		y := num(yearRE.FindString(title))
		if y != year && y != year+1 {
			return
		}
		u, e := url.Parse(attr(n, "href"))
		if e != nil {
			return
		}
		u = base.ResolveReference(u)
		if u.Scheme == "https" && u.Host == "www.stats.gov.cn" {
			found[y] = u.String()
		}
	})
	if found[year] == "" {
		return nil, errors.New("未找到当年的统计局官方日程，保留已有数据")
	}
	links := []string{found[year]}
	if found[year+1] != "" {
		links = append(links, found[year+1])
	}
	return links, nil
}
func Parse(source, rawURL, body string, year int) ([]Event, error) {
	var events []Event
	var err error
	if source == "bls" {
		events, err = parseICS(body, rawURL)
	} else {
		root, e := html.Parse(strings.NewReader(body))
		if e != nil {
			return nil, e
		}
		switch source {
		case "nbs":
			events, err = parseNBS(root, rawURL)
		case "fomc":
			events, err = parseFOMC(root, rawURL, year)
		case "bea":
			events, err = parseBEA(root, rawURL)
		case "nyfed":
			events, err = parseNYFed(root, rawURL)
		default:
			err = errors.New("未知来源")
		}
	}
	if err != nil {
		return nil, err
	}
	if len(events) == 0 {
		return nil, errors.New("未识别到日程，需核查官方页面结构")
	}
	if len(events) > 3000 {
		return nil, errors.New("日程条目数量异常")
	}
	seen := map[string]bool{}
	for _, e := range events {
		if err = e.Validate(); err != nil {
			return nil, fmt.Errorf("官方日程格式变化：%w", err)
		}
		if seen[e.SourceID] {
			return nil, errors.New("日程标识重复，保留上次数据")
		}
		seen[e.SourceID] = true
	}
	return events, nil
}
func attr(n *html.Node, key string) string {
	for _, a := range n.Attr {
		if a.Key == key {
			return a.Val
		}
	}
	return ""
}
func hasClass(n *html.Node, s string) bool {
	return strings.Contains(" "+attr(n, "class")+" ", " "+s+" ")
}
func walk(n *html.Node, f func(*html.Node)) {
	f(n)
	for c := n.FirstChild; c != nil; c = c.NextSibling {
		walk(c, f)
	}
}
func content(n *html.Node) string {
	var b strings.Builder
	walk(n, func(v *html.Node) {
		if v.Type == html.TextNode {
			b.WriteString(v.Data)
			b.WriteByte(' ')
		}
	})
	return strings.Join(strings.Fields(b.String()), " ")
}
func first(n *html.Node, p func(*html.Node) bool) *html.Node {
	var found *html.Node
	walk(n, func(v *html.Node) {
		if found == nil && p(v) {
			found = v
		}
	})
	return found
}
func textClass(n *html.Node, c string) string {
	v := first(n, func(v *html.Node) bool { return hasClass(v, c) })
	if v == nil {
		return ""
	}
	return content(v)
}
func num(s string) int                { v, _ := strconv.Atoi(s); return v }
func dateParts(y, m, d string) string { return fmt.Sprintf("%04d-%02d-%02d", num(y), num(m), num(d)) }
func event(source, id, title, rawURL, zone string) Event {
	return Event{Source: source, SourceID: id, Title: title, Kind: "macro", Timezone: zone, Precision: "date", Status: "scheduled", Importance: 3, URL: rawURL}
}
func localTime(date, clock, zone string) (string, error) {
	loc, err := time.LoadLocation(zone)
	if err != nil {
		return "", err
	}
	t, err := time.ParseInLocation("2006-01-02 15:04", date+" "+clock, loc)
	if err != nil {
		return "", err
	}
	return t.Format(time.RFC3339), nil
}

var yearRE = regexp.MustCompile(`(20[0-9]{2})`)
var dayRE = regexp.MustCompile(`([0-9]{1,2})\s*/`)

// Expand row/column spans before reading the NBS two-row date/time cells.
func tableGrid(table *html.Node) [][]string {
	grid := [][]string{}
	ri := 0
	walk(table, func(row *html.Node) {
		if row.Data != "tr" {
			return
		}
		for len(grid) <= ri {
			grid = append(grid, make([]string, 32))
		}
		col := 0
		for cell := row.FirstChild; cell != nil; cell = cell.NextSibling {
			if cell.Data != "td" && cell.Data != "th" {
				continue
			}
			for col < 32 && grid[ri][col] != "" {
				col++
			}
			rs := max(1, num(attr(cell, "rowspan")))
			cs := max(1, num(attr(cell, "colspan")))
			if rs > 8 || cs > 32 {
				continue
			}
			v := content(cell)
			if v == "" {
				v = " "
			}
			for r := ri; r < ri+rs; r++ {
				for len(grid) <= r {
					grid = append(grid, make([]string, 32))
				}
				for c := col; c < col+cs && c < 32; c++ {
					grid[r][c] = v
				}
			}
			col += cs
		}
		ri++
	})
	return grid
}
func parseNBS(root *html.Node, rawURL string) ([]Event, error) {
	title := first(root, func(n *html.Node) bool { return n.Data == "title" })
	if title == nil {
		return nil, errors.New("统计局标题缺失")
	}
	titleText := content(title)
	if meta := first(root, func(n *html.Node) bool { return n.Data == "meta" && attr(n, "name") == "ArticleTitle" }); meta != nil {
		titleText = attr(meta, "content")
	}
	match := yearRE.FindStringSubmatch(titleText)
	if len(match) == 0 {
		return nil, errors.New("统计局日程年份缺失")
	}
	year := match[1]
	table := first(root, func(n *html.Node) bool {
		return n.Data == "table" && strings.Contains(content(n), "居民消费价格指数")
	})
	if table == nil {
		return nil, errors.New("统计局日程表缺失")
	}
	grid := tableGrid(table)
	out := []Event{}
	for i, row := range grid {
		if i+1 >= len(grid) {
			break
		}
		title := row[1]
		if title != "国民经济运行情况" && !strings.Contains(title, "居民消费价格指数") && !strings.Contains(title, "工业生产者价格指数") && !strings.Contains(title, "采购经理指数") {
			continue
		}
		if len(dayRE.FindStringSubmatch(row[2])) == 0 && row[2] != "……" {
			continue
		}
		for m := 1; m <= 12; m++ {
			days := dayRE.FindAllStringSubmatch(row[m+1], -1)
			for ordinal, d := range days {
				e := event("nbs", fmt.Sprintf("%s:%s:%02d:%d", year, title, m, ordinal), title, rawURL, "Asia/Shanghai")
				e.Start = dateParts(year, strconv.Itoa(m), d[1])
				e.End = e.Start
				e.Evidence = fmt.Sprintf("%s年；%d月：%s；时间：%s。以官方后续修订为准。", year, m, row[m+1], grid[i+1][m+1])
				clock := strings.ReplaceAll(grid[i+1][m+1], "：", ":")
				if regexp.MustCompile(`^\d{1,2}:\d{2}$`).MatchString(clock) {
					t, err := localTime(e.Start, clock, e.Timezone)
					if err != nil {
						return nil, err
					}
					e.Start = t
					e.End = t
					e.Precision = "minute"
				}
				out = append(out, e)
			}
		}
	}
	return out, nil
}
func parseFOMC(root *html.Node, rawURL string, minYear int) ([]Event, error) {
	out := []Event{}
	var parseErr error
	walk(root, func(panel *html.Node) {
		if !hasClass(panel, "panel-default") {
			return
		}
		heading := textClass(panel, "panel-heading")
		if !strings.Contains(heading, "FOMC Meetings") {
			return
		}
		ym := yearRE.FindString(heading)
		year := num(ym)
		if year < minYear {
			return
		}
		occurrences := map[string]int{}
		walk(panel, func(row *html.Node) {
			if !hasClass(row, "fomc-meeting") {
				return
			}
			month := textClass(row, "fomc-meeting__month")
			occurrences[month]++
			days := textClass(row, "fomc-meeting__date")
			clean := strings.TrimSpace(strings.TrimSuffix(days, "*"))
			parts := strings.Split(clean, "-")
			if len(parts) > 2 || num(parts[0]) == 0 {
				parseErr = errors.New("FOMC 日期格式变化")
				return
			}
			months := strings.Split(month, "/")
			startMonth := months[0]
			endMonth := months[len(months)-1]
			start, err := time.Parse("2006 January 2", fmt.Sprintf("%d %s %s", year, startMonth, parts[0]))
			end, e2 := time.Parse("2006 January 2", fmt.Sprintf("%d %s %s", year, endMonth, parts[len(parts)-1]))
			if err != nil || e2 != nil {
				parseErr = errors.New("FOMC 日期格式变化")
				return
			}
			e := event("fomc", fmt.Sprintf("%d:%s:%d", year, month, occurrences[month]), "FOMC 议息会议", rawURL, "America/New_York")
			e.Kind = "monetary"
			e.Start = start.Format("2006-01-02")
			e.End = end.Format("2006-01-02")
			e.Evidence = fmt.Sprintf("%d %s %s。官方会议日程仅列日期；不据此推算决议或发布会时刻。", year, month, days)
			if strings.Contains(days, "*") {
				e.Title += "（含经济预测）"
			}
			out = append(out, e)
		})
	})
	return out, parseErr
}
func parseBEA(root *html.Node, rawURL string) ([]Event, error) {
	table := first(root, func(n *html.Node) bool { return attr(n, "id") == "release-schedule-table" })
	if table == nil {
		return nil, errors.New("BEA 日程表缺失")
	}
	year := yearRE.FindString(content(table))
	out := []Event{}
	var parseErr error
	walk(table, func(row *html.Node) {
		if row.Data != "tr" {
			return
		}
		title := textClass(row, "release-title")
		if title == "" {
			return
		}
		date := textClass(row, "release-date")
		cell := first(row, func(n *html.Node) bool { return hasClass(n, "scheduled-date") })
		if cell == nil {
			parseErr = errors.New("BEA 日期列缺失")
			return
		}
		clockNode := first(cell, func(n *html.Node) bool { return n.Data == "small" })
		if clockNode == nil {
			parseErr = errors.New("BEA 发布时间缺失")
			return
		}
		clock := content(clockNode)
		loc, _ := time.LoadLocation("America/New_York")
		t, err := time.ParseInLocation("2006 January 2 3:04 PM", year+" "+date+" "+clock, loc)
		if err != nil {
			parseErr = errors.New("BEA 时间格式变化")
			return
		}
		e := event("bea", Key(title), title, rawURL, "America/New_York")
		e.Precision = "minute"
		e.Start = t.Format(time.RFC3339)
		e.End = e.Start
		e.Evidence = year + " " + date + " " + clock + " Eastern Time"
		out = append(out, e)
	})
	return out, parseErr
}

func parseICS(body, rawURL string) ([]Event, error) {
	if !strings.Contains(body, "BEGIN:VCALENDAR") {
		return nil, errors.New("BLS 未返回 iCalendar")
	}
	body = strings.ReplaceAll(body, "\r\n", "\n")
	body = strings.ReplaceAll(strings.ReplaceAll(body, "\n ", ""), "\n\t", "")
	out := []Event{}
	fields := map[string]string{}
	params := map[string]string{}
	inside := false
	for _, line := range strings.Split(body, "\n") {
		if line == "BEGIN:VEVENT" {
			inside = true
			fields = map[string]string{}
			params = map[string]string{}
			continue
		}
		if line == "END:VEVENT" && inside {
			inside = false
			if fields["RRULE"] != "" || fields["RECURRENCE-ID"] != "" || fields["RDATE"] != "" || fields["EXDATE"] != "" {
				return nil, errors.New("BLS 出现重复规则，需核查后再导入")
			}
			e := event("bls", fields["UID"], icsText(fields["SUMMARY"]), rawURL, "America/New_York")
			if e.SourceID == "" {
				return nil, errors.New("BLS 日程缺少 UID")
			}
			raw := fields["DTSTART"]
			p := params["DTSTART"]
			if strings.Contains(p, "VALUE=DATE") || len(raw) == 8 {
				t, err := time.Parse("20060102", raw)
				if err != nil {
					return nil, err
				}
				e.Start = t.Format("2006-01-02")
				e.End = e.Start
				if rawEnd := fields["DTEND"]; rawEnd != "" {
					end, err := time.Parse("20060102", rawEnd)
					if err != nil {
						return nil, err
					}
					e.End = end.AddDate(0, 0, -1).Format("2006-01-02")
				}
			} else {
				zone := "America/New_York"
				if strings.Contains(p, "TZID=") {
					zone = strings.Split(strings.Split(p, "TZID=")[1], ";")[0]
				}
				if zone == "US-Eastern" || zone == "US/Eastern" || zone == "Eastern Standard Time" {
					zone = "America/New_York"
				}
				loc, err := time.LoadLocation(zone)
				if err != nil {
					return nil, errors.New("BLS 时区无法识别")
				}
				layout := "20060102T150405"
				if strings.HasSuffix(raw, "Z") {
					loc = time.UTC
					zone = "UTC"
					layout += "Z"
				}
				t, err := time.ParseInLocation(layout, raw, loc)
				if err != nil {
					return nil, err
				}
				e.Timezone = zone
				e.Start = t.Format(time.RFC3339)
				e.End = e.Start
				e.Precision = "minute"
				if rawEnd := fields["DTEND"]; rawEnd != "" {
					if params["DTEND"] != params["DTSTART"] || strings.HasSuffix(rawEnd, "Z") != strings.HasSuffix(raw, "Z") {
						return nil, errors.New("BLS 区间起止时区不一致，需核查")
					}
					end, err := time.ParseInLocation(layout, rawEnd, loc)
					if err != nil {
						return nil, err
					}
					e.End = end.Format(time.RFC3339)
				}
			}
			if fields["STATUS"] == "CANCELLED" {
				e.Status = "cancelled"
			} else if fields["STATUS"] == "CONFIRMED" {
				e.Status = "confirmed"
			}
			e.Evidence = icsText(fields["DESCRIPTION"])
			if e.Evidence == "" {
				e.Evidence = e.Title + "; DTSTART" + p + ":" + raw
			}
			out = append(out, e)
			continue
		}
		if inside {
			left, right, ok := strings.Cut(line, ":")
			if !ok {
				continue
			}
			key, par, _ := strings.Cut(left, ";")
			fields[key] = right
			params[key] = par
		}
	}
	if inside {
		return nil, errors.New("BLS 日程文件不完整")
	}
	return out, nil
}
func icsText(s string) string {
	return strings.NewReplacer(`\n`, "\n", `\N`, "\n", `\,`, ",", `\;`, ";", `\\`, `\`).Replace(s)
}
