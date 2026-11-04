package calendar

import (
	"regexp"
	"strings"
)

var fundCode = regexp.MustCompile(`(?:基金代码|基金主代码|份额代码)[：: \t]*([0-9]{6})(?:[^0-9]|$)`)
var absoluteRange = regexp.MustCompile(`(20[0-9]{2})年([0-9]{1,2})月([0-9]{1,2})日[（(]?[^。\n]{0,12}?[至到—–~～]+\s*(20[0-9]{2})年([0-9]{1,2})月([0-9]{1,2})日`)

// ExtractFund is intentionally conservative: suggestions are not calendar events.
// Abbreviated/relative dates and multiple share classes require human input.
func ExtractFund(id, text, rawURL string) Draft {
	e := Event{Source: "fund", Kind: "fund", Title: "基金开放窗口", Timezone: "Asia/Shanghai", Precision: "date", Status: "confirmed", Importance: 2, URL: rawURL, Evidence: text}
	warnings := []string{"这是规则提取草稿。请逐项核对原文，尤其是申购与赎回是否使用相同窗口；不会推算下一开放周期。"}
	codes := fundCode.FindAllStringSubmatch(text, -1)
	unique := map[string]bool{}
	for _, c := range codes {
		unique[c[1]] = true
	}
	if len(unique) == 1 {
		for c := range unique {
			e.FundCode = c
		}
	} else {
		warnings = append(warnings, "基金代码缺失或包含多个份额，请填写本次录入的代码。")
	}
	ranges := absoluteRange.FindAllStringSubmatch(text, -1)
	if len(ranges) == 1 {
		r := ranges[0]
		e.Start = dateParts(r[1], r[2], r[3])
		e.End = dateParts(r[4], r[5], r[6])
	} else {
		warnings = append(warnings, "未找到唯一的完整日期区间；请根据公告填写，不要填公告发布日期。")
	}
	for _, line := range strings.FieldsFunc(text, func(r rune) bool { return r == '。' || r == '\n' }) {
		if strings.Contains(line, "限") || strings.Contains(line, "不得") || strings.Contains(line, "暂停") || strings.Contains(line, "工作日") || strings.Contains(line, "交易日") {
			e.Conditions += strings.TrimSpace(line) + "。\n"
		}
	}
	warnings = append(warnings, "限制条件仅为候选摘录，请补全金额、渠道、份额和交易日等要求。原文将完整保留。")
	return Draft{ID: id, Text: text, URL: rawURL, Event: e, Warnings: warnings}
}
