---
title: Codex checkpoint - 2026-11-16T16-08-01Z - 后端验收基线复用已提交独立 PR
type: coding_session
permalink: finevent/codex/finevent/codex-checkpoint-2026-11-16-t16-08-01-z-后端验收基线复用已提交独立-pr
status: open
project: finevent
started: '2026-11-16T16:08:01Z'
capture: deliberate
agent: codex
repository: finevent
branch: codex/reuse-release-baseline
git_sha: bd456d6b6cdab677461ed3ecf6e9da23df634623
pull_request_number: '5'
pull_request_title: 'feat(deploy): 后端发布复用七天内的验收基线'
pull_request_url: https://github.com/Liranius/finevent/pull/5
pull_request_state: OPEN
pull_request_base: main
pull_request_head: codex/reuse-release-baseline
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-16T16-08-01Z - 后端验收基线复用已提交独立 PR

## Summary

后端验收在最终重启后记录下一次发布可以校验的观察证据。独立 PR 已提交，远端 CI 正在运行。

## Story

连续两次后端发布原本可能各等一次 65 分钟的基线观察。新流程用已审核前序报告绑定证据摘要，在安装前验证七天有效期、当前运行身份及连续资源历史。缺少可信窗口时，服务器在同一次操作内先做只读观察。交互式 AI 的最后一次重启在付费请求之后，额外的只读观察从该次重启开始。

## Working State

实现、测试、运行说明和决策已推送到 `codex/reuse-release-baseline`。本次提交不启动生产发布。纯前端发布沿用独立前序记录。

## Changed Files

实现入口为 `deploy/persistence_baseline.py`、`deploy/calendar_release.py`、`deploy/news_release.py` 和 `deploy/ai_release.py`。回归入口为 `deploy/test_release_baseline.py` 和 `deploy/test_web_release.py`。操作说明见 `README.md`。

## Verification

部署测试执行 269 项，本地通过 263 项，跳过 6 项平台用例。项目 75 项单测、格式、Lint、构建和提交前门禁均已通过。

## References

- [Finevent 仓库](https://github.com/Liranius/finevent)
- [实现提交](https://github.com/Liranius/finevent/commit/bd456d6b6cdab677461ed3ecf6e9da23df634623)
- [后端基线复用 PR #5](https://github.com/Liranius/finevent/pull/5)

## Observations

- [decision] 只复用完整且仍适用于当前安装的后端观察窗口。新的 OOM 和已知的内存压力继续阻断安装。
- [next_step] 核对 PR 最新提交的 Quality 检查结果，再由维护者审阅。

## Relations

- relates_to [[后端验收观察为下次发布保留七天基线]]
