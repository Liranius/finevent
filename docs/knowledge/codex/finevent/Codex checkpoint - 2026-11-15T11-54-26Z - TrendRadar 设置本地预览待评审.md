---
title: Codex checkpoint - 2026-11-15T11-54-26Z - TrendRadar 设置本地预览待评审
type: coding_session
permalink: finevent/codex/finevent/codex-checkpoint-2026-11-15-t11-54-26-z-trend-radar-设置本地预览待评审
status: open
project: finevent
started: '2026-11-15T11:54:26Z'
capture: deliberate
agent: codex
repository: finevent
branch: codex/collector-observation-checkpoint
git_sha: 18eab8fbeb8638b70bf7b1f26718b0236f2498f1
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-15T11-54-26Z - TrendRadar 设置本地预览待评审

## Summary

Finevent 设置页已在隔离的本地预览中接通真实配置读写服务，维护者可以直接评审来源启停、日报关键词及健康状态的联动。

## Story

维护者希望在 Finevent 内管理现有 TrendRadar 新闻来源和日报关键词，并让健康页和发布验收按实际启用的来源检查。首版沿用原采集频率。评审时使用发布构建启动 Finevent，为配置服务提供独立的 YAML、关键词文件和历史数据库。健康页展示两轮示例采集记录，便于比较成功、失败、停用和刚启用的状态。

## Working State

Git 分支为 `codex/collector-observation-checkpoint`，本地 HEAD 为 `18eab8fbeb8638b70bf7b1f26718b0236f2498f1`。功能代码含已修改和未跟踪文件，当前提交不包含完整实现。设置入口为 `/finevent/#/settings/trendradar`。预览服务、临时数据库和登录态均为本机临时状态，预览未启用网络采集。远端 PR 状态未经核实。

## Changed Files

- `apps/web/src/components/TrendRadarSettings.vue`、`apps/server/internal/probe/trend_settings.go` 和 `deploy/trend_settings.py` 分别承载设置表单、应用接口和受限配置读写。
- `apps/server/internal/probe/source_health_http.go` 和 `deploy/source_health_release.py` 使用来源配置检查健康状态和发布条件。
- `.validation/preview-trendradar-review/serve.py` 是本地评审启动脚本，受 Git 忽略。它需要已有 Linux 发布构建、Python、PyYAML 及套接字启动工具。

## Verification

从宿主系统访问预览返回 HTTP 200。在浏览器登录后，启用掘金并保存，健康摘要由三个启用来源增至四个，掘金显示等待下一轮采集。恢复上一版后，启用数回到三个。另行编辑并保存日报关键词，恢复上一版后核对原有规则已经还原。本轮检查使用示例数据。

## Observations

- [result] 两项配置均经过浏览器、Finevent 接口和配置服务完成保存和恢复，评审页面已保留。
- [next_step] 维护者在设置页评审来源开关、日报关键词及来源健康状态。

## Relations

- relates_to [[通过 Finevent 管理 TrendRadar 来源和日报关键词]]
