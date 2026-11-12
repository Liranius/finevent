# 官方日程解析样本

2026-10-30 从官方网页提取必要片段，删除样式以控制体积；日期、文本、表格合并关系及结构保留。仅用于解析回归，不作为生产静态日程。

- `nbs.html`：[统计局 2026 年日程](https://www.stats.gov.cn/xw/tjxw/tzgg/202512/t20251224_1962137.html)，覆盖 3 月两次 PMI 和 8 月特殊发布时间。
- `fomc.txt`：[FOMC 日程](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)中的 2026 年会议片段；原网页含不规范属性，保留原始文本验证 HTML 解析容错。
- `bea.html`：[BEA 日程](https://www.bea.gov/news/schedule)，覆盖当前表格和冬令时。
- `nyfed-oct2026.html`、`nyfed-nov2026.html`：2026-10-30 从[纽约联储当前页](https://www.newyorkfed.org/research/calendars/nationalecon_cal)和 [11 月页](https://www.newyorkfed.org/research/calendars/i-nov26.html)提取月份、导航与完整日历表，覆盖 5/6 项 BLS 事件和夏令时切换；只整理空白，不更改日期或链接。

2026-10-31 官网首页提前显示 11 月，增加其实际“上一月”链接 `/research/calendars/i-oct26.html` 到 11 月样本，复现并验证月末回取当月行为。跨年测试中的月份替换属于路由边界模拟，不作为真实 2027 年日程。

BLS 的 iCalendar 示例在测试函数中构造，属于协议边界测试，不能视为真实来源连通性证据。
