---
title: 通过 Finevent 管理 TrendRadar 来源和日报关键词
type: decision
permalink: finevent/codex/decisions/通过-finevent-管理-trend-radar-来源和日报关键词
status: accepted
decided: '2026-11-15'
project: finevent
sources:
  - deploy/trend_settings.py
  - deploy/calendar_release.py
  - apps/server/internal/probe/trend_settings.go
  - apps/web/src/components/TrendRadarSettings.vue
  - docs/source-health-api.md
tags:
  - trendradar
  - settings
---

# 通过 Finevent 管理 TrendRadar 来源和日报关键词

用户确认在 Finevent 内编辑现有热榜来源开关和日报关键词，继续沿用半小时采集。独立配置编辑器需要人工导出和替换文件；绑定目录内的两个配置文件可以在下一次定时启动时由 TrendRadar 直接读取。日报筛选只影响报告，Finevent 仍从新闻数据库导入已采集的报道。

Web 服务通过本机受限写入程序提交变更，各项独立校验版本并可恢复上一版。关键词编辑器提供规则说明与示例。来源健康集中在「采集通道状态」，结合已启用清单和实际采集证据，保留停用前的历史。新发布验收需按该轮启动时应启用的具体 ID 核对来源，避免来源数量相同掩盖错采，也避免配置变化改写此前的判断。

## Observations

- [decision] 本次纳入既有来源启停、日报关键词编辑、来源健康与发布验收，采集频率沿用现有设置。
- [consequence] TrendRadar 设置确认配置已保存，「采集通道状态」展示下一轮的实际采集结果。
- [branch] codex/trendradar-settings
