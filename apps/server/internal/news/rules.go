// Package news provides deterministic, explainable topic labels, not investment conclusions.
package news

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"slices"
	"strings"
	"unicode"
)

const Version = "topics-v1"

type Label struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}
type Rule struct {
	Label
	Words      []string
	Importance int
}

var categories = []Rule{
	{Label{"macro", "宏观经济"}, []string{"国内生产总值", "GDP", "CPI", "PPI", "PMI", "非农", "就业报告", "失业率", "通胀", "经济增长"}, 2},
	{Label{"monetary", "货币与流动性"}, []string{"美联储", "央行", "降准", "降息", "加息", "逆回购", "LPR", "MLF", "货币政策"}, 2},
	{Label{"fiscal", "财政与监管"}, []string{"财政部", "财政政策", "证监会", "金融监管", "专项债", "印花税", "税收", "监管处罚"}, 2},
	{Label{"industry_policy", "产业政策"}, []string{"产业政策", "工信部", "发改委", "产业补贴", "产业规划", "设备更新", "以旧换新"}, 2},
	{Label{"company", "公司事件"}, []string{"业绩", "财报", "净利润", "分红", "股份回购", "回购股份", "股票回购", "回购注销", "公司回购", "回购计划", "增持", "减持", "并购", "重组", "股东", "董事会"}, 1},
	{Label{"flows", "资金与交易制度"}, []string{"融资融券", "两融", "沪深港通", "北向资金", "南向资金", "交易规则", "涨跌幅限制", "退市", "IPO", "ETF"}, 2},
	{Label{"overseas", "海外市场"}, []string{"美股", "纳斯达克", "标普", "道琼斯", "港股", "恒生", "日经", "欧股", "美债", "美元指数"}, 1},
	{Label{"trade", "地缘与贸易"}, []string{"关税", "贸易战", "出口管制", "制裁", "军事冲突", "停火", "俄乌", "中东", "战争"}, 2},
	{Label{"commodities", "商品与供应链"}, []string{"原油", "黄金", "铜价", "铁矿石", "天然气", "供应链", "运价", "OPEC", "减产", "大宗商品"}, 1},
	{Label{"emergency", "突发事件"}, []string{"地震", "洪灾", "台风", "重大事故", "紧急状态", "公共卫生事件", "爆炸", "大面积停电"}, 2},
}
var industries = []Rule{
	{Label{"finance", "金融"}, []string{"银行", "券商", "保险", "证券公司"}, 1},
	{Label{"property", "房地产"}, []string{"房地产", "楼市", "房贷", "住宅", "地产"}, 1},
	{Label{"technology", "科技与半导体"}, []string{"半导体", "芯片", "人工智能", "AI", "算力", "软件", "集成电路"}, 1},
	{Label{"energy", "能源"}, []string{"原油", "石油", "天然气", "煤炭", "电力"}, 1},
	{Label{"new_energy", "新能源"}, []string{"光伏", "风电", "储能", "锂电", "动力电池"}, 1},
	{Label{"auto", "汽车"}, []string{"汽车", "车企", "自动驾驶"}, 1},
	{Label{"health", "医药医疗"}, []string{"医药", "药品", "创新药", "医疗", "疫苗"}, 1},
	{Label{"consumer", "消费"}, []string{"消费", "白酒", "零售", "家电", "旅游", "食品"}, 1},
	{Label{"defense", "军工"}, []string{"军工", "国防", "军费", "航空发动机"}, 1},
	{Label{"transport", "交通运输"}, []string{"航运", "运价", "物流", "铁路", "航空公司"}, 1},
	{Label{"agriculture", "农业"}, []string{"农业", "粮食", "生猪", "大豆", "玉米"}, 1},
	{Label{"materials", "金属与材料"}, []string{"钢铁", "铜价", "铝价", "黄金", "铁矿石", "稀土", "化工"}, 1},
}

type Result struct {
	Categories []string `json:"categories"`
	Industries []string `json:"industries"`
	Importance int      `json:"importance"`
	Reasons    []string `json:"reasons"`
	Version    string   `json:"rule_version"`
}

func Categories() []Label {
	out := []Label{}
	for _, r := range categories {
		out = append(out, r.Label)
	}
	return out
}
func Industries() []Label {
	out := []Label{}
	for _, r := range industries {
		out = append(out, r.Label)
	}
	return out
}
func contains(text, word string) bool {
	text, word = strings.ToLower(text), strings.ToLower(word)
	if strings.IndexFunc(word, func(r rune) bool { return r > 127 }) >= 0 {
		return strings.Contains(text, word)
	}
	isWord := func(b byte) bool { return b >= 'a' && b <= 'z' || b >= '0' && b <= '9' || b == '_' }
	for offset := 0; offset < len(text); {
		index := strings.Index(text[offset:], word)
		if index < 0 {
			return false
		}
		index += offset
		end := index + len(word)
		if (index == 0 || !isWord(text[index-1])) && (end == len(text) || !isWord(text[end])) {
			return true
		}
		offset = end
	}
	return false
}
func Classify(title, body string) Result {
	r := Result{Categories: []string{}, Industries: []string{}, Importance: 1, Reasons: []string{}, Version: Version}
	text := title + "\n" + body
	for _, rule := range categories {
		for _, word := range rule.Words {
			if contains(text, word) {
				r.Categories = append(r.Categories, rule.ID)
				r.Importance = max(r.Importance, rule.Importance)
				r.Reasons = append(r.Reasons, rule.Name+"：命中“"+word+"”")
				break
			}
		}
	}
	for _, rule := range industries {
		for _, word := range rule.Words {
			if contains(text, word) {
				r.Industries = append(r.Industries, rule.ID)
				r.Reasons = append(r.Reasons, rule.Name+"：提及“"+word+"”")
				break
			}
		}
	}
	for _, word := range []string{"降准", "降息", "加息", "非农", "CPI", "GDP", "战争", "出口管制", "重大事故"} {
		if contains(title, word) {
			r.Importance = 3
			r.Reasons = append(r.Reasons, "重要性：标题命中“"+word+"”")
			break
		}
	}
	return r
}
func Normalize(text string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return -1
		}
		return unicode.ToLower(r)
	}, text)
}
func Fingerprint(body string) string {
	text := Normalize(body)
	if len([]rune(text)) < 40 {
		return ""
	}
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

var numbers = regexp.MustCompile(`[0-9]+(?:\.[0-9]+)?%?`)

func Similar(a, b string) bool {
	a, b = Normalize(a), Normalize(b)
	if len([]rune(a)) < 12 || len([]rune(b)) < 12 {
		return false
	}
	an, bn := numbers.FindAllString(a, -1), numbers.FindAllString(b, -1)
	slices.Sort(an)
	slices.Sort(bn)
	if !slices.Equal(an, bn) {
		return false
	}
	for _, word := range []string{"上调", "下调", "上涨", "下跌", "增长", "下降", "否认", "暂停", "恢复", "取消", "未", "不"} {
		if strings.Contains(a, word) != strings.Contains(b, word) {
			return false
		}
	}
	grams := func(text string) map[string]bool {
		runes := []rune(text)
		out := map[string]bool{}
		for i := 1; i < len(runes); i++ {
			out[string(runes[i-1:i+1])] = true
		}
		return out
	}
	x, y := grams(a), grams(b)
	same := 0
	for g := range x {
		if y[g] {
			same++
		}
	}
	return 2*same*100 >= 82*(len(x)+len(y))
}
