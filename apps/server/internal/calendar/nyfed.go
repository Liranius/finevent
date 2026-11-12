package calendar

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"golang.org/x/net/html"
)

const nyfedURL = "https://www.newyorkfed.org/research/calendars/nationalecon_cal"
const nyfedNote = "补充当前及下一已发布月份中的 BLS 关键事件，非完整 BLS 日程；不表示 BLS 主源恢复。"

func nyfedMonth(root *html.Node) (time.Time, error) {
	n := first(root, func(n *html.Node) bool { return hasClass(n, "ts-data-table-head") })
	if n == nil {
		return time.Time{}, errors.New("纽约联储日历月份缺失")
	}
	m, err := time.Parse("January 2006", content(n))
	if err != nil {
		return time.Time{}, errors.New("纽约联储日历月份格式变化")
	}
	return m, nil
}

func nyfedNext(body, address string) (string, time.Time, error) {
	return nyfedAdjacent(body, address, 1)
}

func nyfedAdjacent(body, address string, offset int) (string, time.Time, error) {
	root, err := html.Parse(strings.NewReader(body))
	if err != nil {
		return "", time.Time{}, err
	}
	m, err := nyfedMonth(root)
	if err != nil {
		return "", m, err
	}
	label := "NEXT MONTH"
	if offset == -1 {
		label = "PREVIOUS MONTH"
	}
	n := first(root, func(n *html.Node) bool {
		return n.Data == "a" && strings.Contains(strings.ToUpper(content(n)), label)
	})
	if n == nil {
		return "", m, nil
	}
	base, _ := url.Parse(address)
	u, err := url.Parse(attr(n, "href"))
	if err != nil {
		return "", m, errors.New("纽约联储相邻月份链接无效")
	}
	u = base.ResolveReference(u)
	expected := "/research/calendars/i-" + strings.ToLower(m.AddDate(0, offset, 0).Format("Jan06")) + ".html"
	if u.Scheme != "https" || u.Host != "www.newyorkfed.org" || u.User != nil || u.Path != expected || u.RawQuery != "" || u.Fragment != "" {
		return "", m, errors.New("纽约联储相邻月份链接变化，需核查")
	}
	return u.String(), m, nil
}

func fetchNYFed(ctx context.Context, client *http.Client, body string, now time.Time) ([]Event, error) {
	root, err := html.Parse(strings.NewReader(body))
	if err != nil {
		return nil, err
	}
	month, err := nyfedMonth(root)
	if err != nil {
		return nil, err
	}
	zone, err := time.LoadLocation("America/New_York")
	if err != nil {
		return nil, err
	}
	now = now.In(zone)
	current := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
	offset := 1
	if month.Equal(current.AddDate(0, 1, 0)) {
		// The landing page can advance before month end. Follow its real previous
		// link to retain the same current/next-month coverage; do not pull month +2.
		offset = -1
	} else if !month.Equal(current) {
		return nil, fmt.Errorf("纽约联储首页月份 %s 不在美国东部当前月 %s 或下月范围，保留已有日程", month.Format("2006-01"), current.Format("2006-01"))
	}
	adjacent, _, err := nyfedAdjacent(body, nyfedURL, offset)
	if err != nil {
		return nil, err
	}
	if adjacent == "" && offset == -1 {
		return nil, errors.New("纽约联储提前切月但缺少上一月链接，保留已有日程")
	}
	events, err := Parse("nyfed", nyfedURL, body, month.Year())
	if err != nil || adjacent == "" {
		return events, err
	}
	adjacentBody, err := Fetch(ctx, client, adjacent)
	if err != nil {
		return nil, err
	}
	adjacentRoot, err := html.Parse(strings.NewReader(adjacentBody))
	if err != nil {
		return nil, err
	}
	adjacentMonth, err := nyfedMonth(adjacentRoot)
	if err != nil {
		return nil, err
	}
	if !adjacentMonth.Equal(month.AddDate(0, offset, 0)) {
		return nil, errors.New("纽约联储相邻月份页面与链接不符")
	}
	other, err := Parse("nyfed", adjacent, adjacentBody, adjacentMonth.Year())
	if err != nil {
		return nil, err
	}
	if offset == -1 {
		return append(other, events...), nil
	}
	return append(events, other...), nil
}

func nyfedIndicator(title string) (string, string) {
	name := strings.ToLower(strings.TrimSpace(title))
	switch {
	case name == "employment situation":
		return "empsit", "美国就业报告（非农）"
	case name == "consumer price index":
		return "cpi", "美国 CPI"
	case strings.HasPrefix(name, "producer price index"):
		return "ppi", "美国 PPI"
	case name == "jolts":
		return "jolts", "美国 JOLTS 职位空缺"
	case name == "imports and exports":
		return "ximpim", "美国进出口价格指数"
	case name == "employment cost index":
		return "eci", "美国就业成本指数"
	case strings.HasPrefix(name, "productivity & costs"):
		if strings.Contains(name, "preliminary") {
			return "prod2-preliminary", "美国生产率与成本（初值）"
		}
		if strings.Contains(name, "revised") {
			return "prod2-revised", "美国生产率与成本（修订）"
		}
		return "prod2", "美国生产率与成本"
	default:
		return "", ""
	}
}

func parseNYFed(root *html.Node, address string) ([]Event, error) {
	month, err := nyfedMonth(root)
	if err != nil {
		return nil, err
	}
	table := first(root, func(n *html.Node) bool { return n.Data == "table" && hasClass(n, "research-table-1col") })
	if table == nil {
		return nil, errors.New("纽约联储日程表缺失")
	}
	var events []Event
	var parseErr error
	dayPattern := regexp.MustCompile(`^(\d{1,2})(?:\s|$)`)
	clockPattern := regexp.MustCompile(`^\((\d{2}:\d{2})\)`)
	walk(table, func(cell *html.Node) {
		if cell.Data != "td" || !hasClass(cell, "somatdR") || parseErr != nil {
			return
		}
		day := dayPattern.FindStringSubmatch(content(cell))
		if len(day) == 0 {
			return
		}
		walk(cell, func(a *html.Node) {
			if a.Data != "a" || parseErr != nil {
				return
			}
			u, e := url.Parse(attr(a, "href"))
			if e != nil || u.Scheme != "https" || u.Host != "www.bls.gov" || u.User != nil {
				return
			}
			indicator, title := nyfedIndicator(content(a))
			if indicator == "" {
				parseErr = errors.New("纽约联储新增 BLS 项目尚未识别，需核查")
				return
			}
			var following strings.Builder
			for n := a.NextSibling; n != nil && n.Data != "a"; n = n.NextSibling {
				following.WriteString(content(n))
				following.WriteByte(' ')
			}
			clock := clockPattern.FindStringSubmatch(strings.TrimSpace(following.String()))
			if len(clock) == 0 {
				parseErr = errors.New("纽约联储事件时刻缺失，不能推算")
				return
			}
			date := fmt.Sprintf("%s-%02d", month.Format("2006-01"), num(day[1]))
			at, e := localTime(date, clock[1], "America/New_York")
			if e != nil {
				parseErr = e
				return
			}
			ev := event("nyfed", month.Format("2006-01")+":"+indicator, title, address, "America/New_York")
			ev.Start, ev.End, ev.Precision = at, at, "minute"
			ev.Evidence = fmt.Sprintf("纽约联储日历：%s，%s %s（美国东部时间）。", content(a), date, clock[1])
			ev.Conditions = nyfedNote
			ev.Provenance = &Provenance{Mode: "supplement", AuthorityURL: u.String()}
			events = append(events, ev)
		})
	})
	return events, parseErr
}
