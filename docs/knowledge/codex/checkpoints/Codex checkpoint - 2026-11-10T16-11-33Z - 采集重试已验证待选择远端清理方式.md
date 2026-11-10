---
title: Codex checkpoint - 2026-11-10T16-11-33Z - 采集重试已验证待选择远端清理方式
type: coding_session
permalink: finevent/codex/checkpoints/codex-checkpoint-2026-11-10-t16-11-33-z-采集重试已验证待选择远端清理方式
status: open
project: finevent
repository: finevent
branch: codex/collector-retry-evidence
git_sha: 9def9f79db88680aa3844d18c205b73f959728a2
started: '2026-11-10T16:11:33Z'
capture: deliberate
agent: codex
trigger: compact
model: gpt-6-astra
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-10T16-11-33Z - 采集重试已验证待选择远端清理方式

## Summary

文档脱敏与采集重试已在本地提交并通过验证，干净分支和仓库配置已备份；用户正考虑删除重建远端，尚未授权任何远端历史或保护规则变更。

## Story

Actions 部署验收完成后，用户授权先处理实际部署地址重新进入公开笔记的问题和过时文档，再改进快讯超时恢复及延迟证据。旧 checkpoint 只移除了真实网址，保留历史叙述；AI、新闻、CI 和 Router 决策的过时状态已更正。生产服务没有再次部署。

采集实现保持正常两分钟周期，对瞬时失败采用持久化、有上限的退避，尊重限流；逐页保存有界 ID、发布时间、网络时间和响应摘要。新闻 API 新增有鉴权的分页证据读取，延迟统计固定首次观察的发布时间。数据库沿用旧 polls 列结构，新增独立证据表，保留旧版写入和清理兼容。具体数值与边界见 docs/news-api.md，真实部署后的恢复效果和长尾原因仍由 implementation-status 的 TODO 跟踪，不能用本地测试关闭。

脱敏候选先仅在本机生成，然后把本次未推送分支移到干净父提交。已确认 rebase 前后最终文件完全相同，当前分支可达的 387 个提交/文件对象对本机已知部署值扫描无匹配；这是已知值检查，不是全能秘密扫描。旧本地 main、远端及来源分支仍有未清理历史，绝不能直接 push --all 或 --mirror。

我曾请求用户批准临时允许强推并豁免管理员 PR/Quality 限制，精确替换 main 和 PR 7 来源分支后恢复保护。用户没有批准，最新问题是“我将 repo 删除后重建是否可解决？”已解释：先清理本地再推送可建立干净新远端，但不能保证抹去外部克隆、fork、缓存或平台留存；新仓库需重新配置部署 secrets、环境和保护，旧 PR/Actions 记录不会自动迁入。当前 GitHub 显示公开仓库、fork 数为 0。未删除仓库、强推、改保护或联系 GitHub Support。

## Working State

写本笔记前工作区干净，分支 codex/collector-retry-evidence 有两个本地、未推送提交：368d340（文档）和 9def9f79db88680aa3844d18c205b73f959728a2（采集及配套文档），无 PR。父提交 685fa73274c7e0566223252afe89bc0c41d1e8cc 是仅脱敏的 main 候选；PR 7 来源分支候选 ae8940b268e3ca487fba5852b18941a9580d0ff2 也仅在本机。原远端仍为 main 97ea4d53166a6492b04ff65a677cb00e860cf028、来源分支 fa2c638180db3f9296a9256259b2ff4837095354。执行远端操作前必须重新查询，不依赖这些快照做盲目覆盖。

机器本地的 .validation/collector-retry/ 保存历史评审、已知值扫描报告、验证日志和已通过 bundle verify 的 clean-local-history.bundle。repository/ 保存仓库设置、保护、Actions 权限、production 环境及允许部署分支、变量和 secret 名称；GitHub 不可读的 secret 值并没有被导出。真实环境配置及专用密钥仍仅在原忽略位置，不能提交。重建时需确认本机原始凭据可用，再恢复环境。没有后台测试进程，只有远端清理方式尚待选择。

## Changed Files

- apps/server/internal/probe/collector.go、runtime.go、store.go、http.go：重试分类、调度、首次发布时间统计和鉴权入口。
- apps/server/internal/probe/collection_evidence.go 与 collection_evidence_test.go：有界证据、持久化、查询和回归。
- README.md、docs/ai-api.md、docs/news-api.md、docs/implementation-status.md：当前状态与行为说明。
- Router 决策笔记已修正后续授权和 CI 引用；新增采集方案决策笔记为 open，尚不代表生产效果已签收。

## Verification

pnpm ci:format、pnpm ci:lint、pnpm test:unit（63 项）、pnpm build、pnpm test:e2e（31 项 Chromium）通过；Go 全量 test 和 vet 通过。后加的迟到条目/发布时间修订回归也单独通过，之后只有文档和无文件差异的本地 rebase。部署 Python 243 项通过，Windows 跳过两项 Linux 专用检查；PowerShell 20 个部署入口及配置回归通过。最初沙箱阻止 httptest 的本机套接字，沙箱外复测成功，不把该环境失败归为应用缺陷。

空白和改动 Markdown 格式通过，提交钩子通过，仅有既有 ESLint 对非 JS 文件的忽略警告。知识检查从任务原基点执行，四个候选已语义核对，无 unlinked 或 warnings；构建/基线两份决策不受本次采集改动影响。没有运行新远端 Quality、部署新版本或取得新生产长尾证据。

## References

- [当前仓库](https://github.com/Liranius/finevent)：写入时仍存在，未重建。
- docs/news-api.md：新采集证据接口与重试边界。
- docs/implementation-status.md：生产验收与剩余 TODO。
- [GitHub 删除仓库说明](https://docs.github.com/en/repositories/creating-and-managing-repositories/deleting-a-repository)
- [GitHub 清除历史敏感数据说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)

## Observations

- [result] 本地采集修复与文档修正已提交，正常周期、旧版写入、失败历史和限流约束有回归覆盖。
- [constraint] 用户最新消息是询问删除重建是否可行，不是删除、强推或豁免保护的授权。
- [blocker] 远端历史清理方式尚未确定；本地 main 与旧远端引用仍不可当作已脱敏发布来源。
- [next_step] 先明确用户选择的远端清理方式，再按该方式恢复或核对保护及 Actions，并通过真实 PR Quality 验证本地采集分支。

## Relations

- continues [[Codex checkpoint - 2026-11-10T15-43-32Z - Actions验收完成后修正文档与采集重试]]
- relates_to [[快讯瞬时失败采用有界重试并保留窗口时间证据]]
