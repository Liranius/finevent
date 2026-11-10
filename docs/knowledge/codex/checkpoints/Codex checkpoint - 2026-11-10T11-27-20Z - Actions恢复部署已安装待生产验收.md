---
title: Codex checkpoint - 2026-11-10T11-27-20Z - Actions恢复部署已安装待生产验收
type: coding_session
permalink: finevent/codex/checkpoints/codex-checkpoint-2026-11-10-t11-27-20-z-actions-恢复部署已安装待生产验收
status: open
project: finevent
repository: finevent
branch: main
git_sha: 372c03dd72c770d9c452dad6d461408bd7d3fb2a
started: '2026-11-10T11:27:20Z'
capture: deliberate
agent: codex
trigger: compact
model: gpt-6-astra
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-10T11-27-20Z - Actions恢复部署已安装待生产验收

## Summary

第二批 GitHub Actions 部署已实际安装生产应用，过期证据通过完整只读复验更新；当前仍在 65 分钟新闻与资源观察窗口，生产浏览器和手机通知验收尚未完成。

## Story

本线程从恢复 finevent 工程基线开始，先引入 Router、暂缓 Pinia，再分两批将仅本地发布扩展到 GitHub Actions，并保留本地入口、通知与旧深链接兼容。第一批通用构建和 Quality 门禁已合并，第二批增加受限 SSH 控制器及 Deploy/Report。首次真实发布在安装前被“固化证据不超过七天”门禁挡住；用户明确授权只读复验与显式恢复流程，要求保留原失败和全部门禁。

PR 5（旧 PR #5） 引入协议 2：只读复验固定容器、配置、进程和资源状态，连续观察 65 分钟、至少两轮采集，再以报告 SHA 绑定后续恢复。历史留存缺口不补造；恢复只允许已停止且完全未进入安装的失败操作。用户已在本机再次执行一次性 bootstrap，报告 Initialization and read-only preflight completed，协议 2 已核实。

等待期间发现原 PWA 的 sw.js 不随资源变化，已通过 PR 6（旧 PR #6） 给构建产物写入内容版本标记，并添加真实升级、离线回载测试。当前部署采用该修复后的主分支 CI 产物。Actions 安装成功只是中间状态，不能据此宣布最终验收完成。

## Working State

持久状态：主分支已合并 PR 5/6。当前 main 为 372c03dd72c770d9c452dad6d461408bd7d3fb2a，工作区干净，无 main 头分支 PR。production 环境和专用受限密钥已设置，无需再次 bootstrap。

只读基线 persistence-check-… 于北京时间 19:14:52 完成，状态 completed_for_review；固定报告 SHA 为 e7b5f9c147bcf61b39665616187de3ccbabed1caec62ec25408598e277c4a807。最终 Report（旧运行 42806152818） 成功。

恢复发布 calendar-release-… 的 Deploy（旧运行 42806423220） 已成功安装。状态 observing / news_and_resources，预计北京时间 20:25:35 结束。源提交为当前 main，通用产物 SHA 为 0b9ca45b00504f4b60a7723b7f636bfc09f64b7f2aba848f2dd825b30fad08ca，内容 SHA 为 dea54def104eb3c50aa38059f57ceadeac661c5e62db162ac8dd503c9f8edad5。after_review 是 calendar-release-…，恢复的失败操作是 calendar-release-…，其原始报告 SHA f91755a7857e00a77a250ab45e8beb021d9a97177dc16b53d2469c9cc7abf674 已核对未改变。

机器本地、易失状态：只读观察进程 正在每 45 秒轮询；脚本 .validation/actions-deploy/watch-operation.py。私有完整报告在 .validation/<operation-id>/report.json；当前摘要在 .validation/actions-deploy/watch-calendar-release-….json；初始安装后报告 recovery-start-report.json、操作身份 recovery-operation.json 与请求 recovery-dispatch.json 同在该目录。私钥和服务器配置均被忽略，不得提交或打印。读取含中文 JSON 必须指定 UTF-8。重启后先重新查询状态，不能依赖旧 session。

Chrome 已有生产页 （实际地址见本机私有配置），已知 tab ID 1510675754。新部署后的 CUA 验收刚开始，文档重载上次输出被截断，恢复时先完整读取 CUA 文档。不得通过清缓存/注销 worker 假装升级成功。旧页面原先已有认证，但新部署后尚未核实。

## Changed Files

主要入口：deploy/persistence_baseline.py、deploy/calendar_release.py、deploy/release_control.py、scripts/release-client.py、scripts/verify-baseline.ps1、publish-calendar/news PowerShell 入口及 Actions Deploy/Report。协议 2 的配对参数、失败身份校验与重试行为已有测试。PWA 修复在 apps/web/scripts/precache.mjs、tests/precache.test.ts、e2e/pwa-update.spec.ts 和 e2e/offline-server.ts。README.md、docs/implementation-status.md 和部署决策笔记仍需在本次最终验收后更新实际结果及剩余 TODO。

## Verification

- 协议 2：243 项 Python 测试（Windows 两项 Linux 专用跳过，Linux CI 通过）、20 项 PowerShell 入口测试、Go test/vet、格式/Lint/单测/Chromium、通用构建和环境渲染通过。PR 5 最新 CI 42794817742、主分支 42795325145 成功。
- PWA 缺陷先由新单测复现失败，再修复。63 项单测、31 项 Chromium E2E、格式/Lint/类型/构建通过；真实 PWA 升级与离线回载测试在 Chromium、Firefox、WebKit 均通过。PR 6 CI 42801914725、当前主分支 CI 42802453888 成功；产物已下载核对摘要，worker 标记 3872c2674e8cdb6b 与 manifest 一致。
- 只读基线实际运行 3902.96 秒、781 样本，最大采样间隔 6 秒，可用内存最低 222.52 MiB，主机及两服务 OOM 增量全零；TrendRadar 两轮/两个半小时桶，60 次应用轮询无失败，HTTPS 200。相同基线 ID 重试未重启观察。
- 实际恢复预检通过，并验证新基线结束后 3 条连续资源历史；备份验证、重启、新闻数据、推送设置、来源健康、关联数据迁移保留检查均 passed。后续完整观察结果尚未产生。
- 知识关联检查已完成 PR 5/6 范围，无未关联候选或警告；此前取消的 PR 5 CI 不代表最新失败。服务器 report 的 user_acceptance 仍 pending，不能伪造为通过。

## References

- [仓库](https://github.com/Liranius/finevent)
- 当前已推送提交（旧提交 372c03dd72c770d9c452dad6d461408bd7d3fb2a）
- 只读基线与恢复 PR 5（旧 PR #5）
- PWA 升级修复 PR 6（MERGED，main ← codex/pwa-release-update）（旧 PR #6）
- 实际 Deploy（旧运行 42806423220）
- 仓库验收入口：README.md、docs/implementation-status.md、deploy/news_release.py。

## Observations

- [result] Actions 已真正安装修复后的应用，原失败报告和只读基线证据均保留并被摘要绑定。
- [decision] 保留七天、资源和采集门禁；新的只读观察只证明新窗口及其后的连续历史，不重建旧留存缺口。
- [decision] Pinia 暂缓，Router 和本地发布入口保留；PWA 更新用构建内容版本改变 worker 字节，重复相同构建保持稳定。
- [blocker] 最终 65 分钟生产观察尚未完成；生产浏览器、PWA 更新、旧深链接/前进后退及实体手机通知检查尚未完成。
- [verification] 本次 news profile 不重复六小时日历观察、两个交易日或 AI 负载验收，不得扩大成功结论。
- [next_step] 继续生产浏览器行为验收并监控当前发布至最终报告；再运行最终 Actions Report、核对不可变证据，更新验收文档与剩余人工事项。

## Relations

- relates_to [[通过只读复验更新部署基线，显式恢复安装前失败]]
