---
title: 美国零售销售用联储月历与 OMB 年表补充发布机构
type: decision
permalink: finevent/codex/decisions/美国零售销售用联储月历与-omb-年表补充发布机构
status: accepted
project: finevent
decided: '2026-11-17'
sources:
  - apps/server/internal/calendar/nyfed.go
  - apps/server/internal/calendar/omb.go
  - apps/server/internal/calendar/retail_schedule.go
  - deploy/calendar_release.py
  - docs/calendar-api.md
tags:
  - calendar
  - decision
---

# 美国零售销售用联储月历与 OMB 年表补充发布机构

Census 公布零售销售初值的统计月份、发布日期及美东时刻。生产出口连续收到 HTTP 403。服务器从纽约联储读到了 2026 年 11 月 17 日和 12 月 16 日的 08:30 日程，也取得了与本地核对结果一致的 OMB 2027 年 PDF。

## Observations

- [decision] 保留 Census 的独立同步和错误记录。纽约联储提供近期时刻，OMB 年表提供全年日期。来源分工与匹配规则见 `docs/calendar-api.md`。
- [rationale] 纽约联储月历的条目没有标明统计月份。Census 的显式数据期有助于处理跨月改期；OMB 只给日期，不能据此设置小时级提醒。
- [constraint] 同期事件沿用原 ID。补充来源成功后，Census 的 403 继续在来源状态中展示。

## Relations

- extends [[零售销售与初请日程使用发布机构的一手安排]]
