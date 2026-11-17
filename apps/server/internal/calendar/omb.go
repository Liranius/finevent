package calendar

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/ledongthuc/pdf"
	"golang.org/x/net/html"
)

const ombURL = "https://www.whitehouse.gov/omb/information-resources/guidance/us-principal-federal-economic-indicators/"

// These published documents can also be fetched directly if the index is unavailable.
var publishedOMB = map[int]string{
	2026: "https://www.whitehouse.gov/wp-content/uploads/2025/09/pfei_schedule_release_dates_cy2026.pdf",
	2027: "https://www.whitehouse.gov/wp-content/uploads/2026/10/FY2027-PFEI.pdf",
}

func ombLinks(body string, year int) (map[int]string, error) {
	root, err := html.Parse(strings.NewReader(body))
	if err != nil {
		return nil, err
	}
	base, _ := url.Parse(ombURL)
	links := map[int]string{}
	walk(root, func(n *html.Node) {
		if n.Data != "a" || err != nil {
			return
		}
		label, ok := strings.CutPrefix(content(n), "Schedule of Release Dates for Principal Federal Economic Indicators for ")
		y, e := strconv.Atoi(label)
		if !ok || e != nil || (y != year && y != year+1) {
			return
		}
		u, e := base.Parse(attr(n, "href"))
		if e != nil || u.Scheme != "https" || u.Host != base.Host || u.User != nil || !strings.HasSuffix(strings.ToLower(u.Path), ".pdf") {
			err = errors.New("OMB 年表链接无效")
			return
		}
		if previous := links[y]; previous != "" && previous != u.String() {
			err = errors.New("OMB 同一年出现多份年表")
			return
		}
		links[y] = u.String()
	})
	if err == nil && links[year] == "" {
		err = errors.New("OMB 索引缺少当年日程")
	}
	return links, err
}

func fetchOMB(ctx context.Context, client *http.Client, index string, indexErr error, year int) ([]Event, error) {
	links := publishedOMB
	if indexErr == nil {
		var err error
		links, err = ombLinks(index, year)
		if err != nil {
			return nil, err
		}
	} else if links[year] == "" {
		return nil, indexErr
	}
	var events []Event
	for y := year; y <= year+1; y++ {
		link := links[y]
		if link == "" {
			continue
		}
		body, err := Fetch(ctx, client, link)
		if err != nil {
			return nil, err
		}
		annual, err := parseOMB(body, link, y)
		if err != nil {
			return nil, err
		}
		events = append(events, annual...)
	}
	return events, nil
}

func parseOMB(body, link string, year int) ([]Event, error) {
	reader, err := pdf.NewReader(strings.NewReader(body), int64(len(body)))
	if err != nil {
		return nil, errors.New("OMB 年表 PDF 无法读取")
	}
	pages := reader.NumPage()
	if pages < 1 || pages > 30 {
		return nil, errors.New("OMB 年表页数异常")
	}
	var result []Event
	for page := 1; page <= pages; page++ {
		rows, err := reader.Page(page).GetTextByRow()
		if err != nil {
			return nil, errors.New("OMB 年表文字无法读取")
		}
		text := make([]string, len(rows))
		for i, row := range rows {
			var line strings.Builder
			for _, part := range row.Content {
				line.WriteString(part.S)
			}
			text[i] = strings.Join(strings.Fields(line.String()), " ")
		}
		annual, err := ombRetailRow(rows, text, link, year)
		if err != nil {
			return nil, err
		}
		if len(annual) > 0 {
			if len(result) > 0 {
				return nil, errors.New("OMB 零售销售行重复")
			}
			result = annual
		}
	}
	if len(result) != 12 {
		return nil, errors.New("OMB 年表缺少完整零售销售日程")
	}
	return result, nil
}

func ombRetailRow(rows pdf.Rows, text []string, link string, year int) ([]Event, error) {
	const label = "Advance Monthly Sales for Retail and Food Services"
	const months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC"
	heading := fmt.Sprintf("PRINCIPAL FEDERAL ECONOMIC INDICATORS FOR %d", year)
	var events []Event
	for i, line := range text {
		if line != label && !strings.HasPrefix(line, label+" ") {
			continue
		}
		headers := strings.Join(text[:i], "\n")
		if len(events) != 0 || !strings.Contains(headers, heading+"\n") || !strings.Contains(headers, months) ||
			i+1 >= len(text) || text[i+1] != "(Data are for previous month)" {
			return nil, errors.New("OMB 零售销售年表标题、月份或统计期变化")
		}
		days := strings.Fields(strings.TrimPrefix(line, label))
		// The 2026 PDF places the numbers one point above their row label.
		if len(days) == 0 && i > 0 && rows[i-1].Position-rows[i].Position <= 1 {
			days = strings.Fields(text[i-1])
		}
		if len(days) != 12 {
			return nil, errors.New("OMB 零售销售行须含十二个月")
		}
		for n, value := range days {
			day, err := strconv.Atoi(value)
			month := time.Date(year, time.Month(n+1), 1, 0, 0, 0, 0, time.UTC)
			if err != nil || day < 1 || day > month.AddDate(0, 1, -1).Day() {
				return nil, errors.New("OMB 零售销售发布日期无效")
			}
			period := month.AddDate(0, -1, 0).Format("2006-01")
			e := event("omb", retailPrefix+period, retailTitle, link, easternZone)
			e.Start = month.AddDate(0, 0, day-1).Format(time.DateOnly)
			e.End, e.Precision = e.Start, "date"
			e.Evidence = fmt.Sprintf("OMB 年表：零售与餐饮服务销售额，数据期 %s，发布日期 %s。年表仅列日期。", period, e.Start)
			events = append(events, e)
		}
	}
	return events, nil
}
