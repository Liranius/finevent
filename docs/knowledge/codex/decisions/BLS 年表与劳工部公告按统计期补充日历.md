---
title: BLS 年表与劳工部公告按统计期补充日历
type: decision
permalink: finevent/codex/decisions/bls-年表与劳工部公告按统计期补充日历
project: finevent
status: accepted
decided: '2026-11-16'
sources:
  - apps/server/internal/calendar/bls_schedule.go
  - apps/server/internal/calendar/snapshots/README.md
  - docs/calendar-api.md
tags:
  - decision
  - calendar
---

# BLS 年表与劳工部公告按统计期补充日历

BLS 日历的直接请求持续收到 403。纽约联储可以同步近期常见指标，但缺少全国非农年度基准初步修订等发布。Finevent 使用公开年表和各月清单交叉核对的日程摘录补全已公布的安排，并保留主源的实际错误和最后成功时间。

同一发布项目和统计期沿用原事件、关注和修订历史。全国与州级初步基准修订分列，最终基准修订附在官方指明的月度就业报告中。已公布的时刻可用于提醒，修订方向和幅度等待实际发布。劳工部托管的就业报告明确确认临近日程时，经人工核对后补充相应事件。

## Observations

- [decision] 年表给出已公布日程的覆盖面，纽约联储持续更新近期可明确识别的指标。具备明确统计期的劳工部公告和 BLS 主源可更新同一事件。
- [rationale] 仅用纽约联储会漏掉不在其覆盖范围内的发布；仅依赖年表无法及时获知临时改期。正文和发布时刻均应可追溯。
- [decision] 取得 BLS 的 ICS 时，可在应用预览并确认变更。保留原文件、事件修订和自动同步的真实状态，文件缺项保留原记录。接口见 `docs/calendar-api.md`。
- [consequence] 只有年表网页或公告 PDF 时，仍可核对并更新随程序发布的摘录。维护入口见 `apps/server/internal/calendar/snapshots/README.md`。

## Relations

- relates_to [[零售销售与初请日程使用发布机构的一手安排]]
