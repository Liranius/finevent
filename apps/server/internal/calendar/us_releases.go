package calendar

import (
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"golang.org/x/net/html"
)

const (
	censusRetailURL = "https://www.census.gov/retail/release_schedule.html"
	dolClaimsURL    = "https://oui.doleta.gov/unemploy/claims_arch.asp"
	easternZone     = "America/New_York"
	retailPrefix    = "advance-retail:"
	retailTitle     = "美国零售销售（初值）"
)

// Both agencies place their two-column schedule immediately after its heading or introduction.
func releaseRows(heading *html.Node) ([][]string, error) {
	if heading != nil {
		for n := heading.NextSibling; n != nil; n = n.NextSibling {
			if n.Type != html.ElementNode {
				continue
			}
			if n.Data != "table" {
				break
			}
			var rows [][]string
			walk(n, func(row *html.Node) {
				if row.Data != "tr" {
					return
				}
				var cells []string
				for cell := row.FirstChild; cell != nil; cell = cell.NextSibling {
					if cell.Data == "td" || cell.Data == "th" {
						cells = append(cells, content(cell))
					}
				}
				rows = append(rows, cells)
			})
			for _, row := range rows {
				if len(row) != 2 {
					return nil, errors.New("官方日程表列数变化")
				}
			}
			if len(rows) >= 2 {
				return rows, nil
			}
			break
		}
	}
	return nil, errors.New("未找到官方发布安排或日程表")
}

func parseRetailSales(root *html.Node, address string) ([]Event, error) {
	heading := first(root, func(n *html.Node) bool {
		return n.Data == "h2" && content(n) == "Advance Monthly Retail Trade Report"
	})
	rows, err := releaseRows(heading)
	if err != nil {
		return nil, err
	}
	clock, ok := strings.CutPrefix(rows[0][1], "Release Date at ")
	if !ok || rows[0][0] != "Data Month" {
		return nil, errors.New("零售销售日程表头变化")
	}
	loc, err := time.LoadLocation(easternZone)
	if err != nil {
		return nil, err
	}
	var events []Event
	for _, row := range rows[1:] {
		period, err := time.Parse("January 2006", row[0])
		if err != nil {
			return nil, fmt.Errorf("零售销售统计月份无效：%w", err)
		}
		if row[1] == "To be announced at a later date" || row[1] == "TBD" {
			continue
		}
		at, err := time.ParseInLocation("January 2, 2006 3:04 PM", row[1]+" "+strings.ToUpper(clock), loc)
		if err != nil {
			return nil, fmt.Errorf("零售销售发布时间无效：%w", err)
		}
		e := event("census", retailPrefix+period.Format("2006-01"), retailTitle, address, easternZone)
		e.Start, e.End, e.Precision = at.Format(time.RFC3339), at.Format(time.RFC3339), "minute"
		e.Evidence = fmt.Sprintf("零售与餐饮服务销售额，数据期 %s，发布于 %s（美国东部时间）。", period.Format("2006-01"), at.Format("2006-01-02 15:04"))
		events = append(events, e)
	}
	return events, nil
}

var weeklyClaimsRule = regexp.MustCompile(`(?i)^Publication Schedule: The UI Weekly Claims News Release is published each week on Thursday morning at (\d{1,2}:\d{2})\s*am (?:EST|ET)\.`)

// The Thursday identifies the release week even when the announced day changes.
func claimsThursday(day time.Time) time.Time {
	mondayOffset := (int(day.Weekday()) + 6) % 7
	return day.AddDate(0, 0, 3-mondayOffset)
}

func parseInitialClaims(root *html.Node, address string, now time.Time) ([]Event, error) {
	intro := first(root, func(n *html.Node) bool {
		return n.Data == "p" && strings.HasPrefix(content(n), "Publication Schedule:")
	})
	rows, err := releaseRows(intro)
	if err != nil {
		return nil, err
	}
	rule := weeklyClaimsRule.FindStringSubmatch(content(intro))
	if rule == nil || rows[0][0] != "Release Date" || rows[0][1] != "Release Time" {
		return nil, errors.New("初请失业金常规安排或例外表头变化")
	}
	clock, err := time.Parse("3:04 PM", rule[1]+" AM")
	if err != nil {
		return nil, err
	}
	loc, err := time.LoadLocation(easternZone)
	if err != nil {
		return nil, err
	}
	exceptions := make(map[string]time.Time)
	for _, row := range rows[1:] {
		const layout = "Monday, January 2, 2006 3:04 PM MST"
		published := row[0] + " " + row[1]
		at, err := time.ParseInLocation(layout, published, loc)
		if err != nil || at.In(loc).Format(layout) != published {
			return nil, errors.New("初请失业金例外发布时间无效")
		}
		key := claimsThursday(at).Format(time.DateOnly)
		if _, exists := exceptions[key]; exists {
			return nil, errors.New("初请失业金同一周有多个例外")
		}
		exceptions[key] = at
	}
	now = now.In(loc)
	start := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, loc)
	end := time.Date(now.Year(), now.Month()+2, 1, 0, 0, 0, 0, loc)
	var events []Event
	for day := claimsThursday(start); !day.After(claimsThursday(end)); day = day.AddDate(0, 0, 7) {
		key := day.Format(time.DateOnly)
		at, shifted := exceptions[key]
		basis := "每周发布安排"
		if shifted {
			basis = "例外发布安排"
		} else {
			at = time.Date(day.Year(), day.Month(), day.Day(), clock.Hour(), clock.Minute(), 0, 0, loc)
		}
		if at.Before(start) || !at.Before(end) {
			continue
		}
		e := event("dol", "initial-claims:"+key, "美国首次申领失业救济人数", address, easternZone)
		e.Start, e.End, e.Precision = at.Format(time.RFC3339), at.Format(time.RFC3339), "minute"
		e.Evidence = fmt.Sprintf("美国劳工部%s，%s（美国东部时间）。", basis, at.Format("2006-01-02 15:04"))
		events = append(events, e)
	}
	return events, nil
}
