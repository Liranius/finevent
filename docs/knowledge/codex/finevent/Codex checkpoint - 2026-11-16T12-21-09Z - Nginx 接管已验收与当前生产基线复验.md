---
title: Codex checkpoint - 2026-11-16T12-21-09Z - Nginx 接管已验收与当前生产基线复验
type: coding_session
permalink: finevent/codex/finevent/codex-checkpoint-2026-11-16-t12-21-09-z-nginx-接管已验收与当前生产基线复验
status: open
project: finevent
started: '2026-11-16T12:21:09Z'
capture: deliberate
agent: codex
repository: finevent
branch: codex/split-web-server-releases
git_sha: c6ff0df6307e886b7ec9bd89205f9e098105f61b
pull_request_number: '2'
pull_request_title: 'feat(deploy): 独立构建、发布和回滚前后端'
pull_request_state: MERGED
pull_request_base: main
pull_request_head: codex/split-web-server-releases
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-16T12-21-09Z - Nginx 接管已验收与当前生产基线复验

## Summary

Nginx 已用现网旧页面完成切换，手机旧 PWA 验收通过，后端发布正在等待与当前服务绑定的只读基线。

## Story

前后端原先共用发布包。拆分已合入 main。生产迁移先保留原 Go 服务，把旧包的静态内容交给 Nginx，再分别发布两份主线产物。新版 Web 增加了 TrendRadar 设置页，需要新版 Server 的读写接口，因此本次采用后端先行的顺序。

## Working State

- 干净工作分支 `codex/split-web-server-releases` 指向 `c6ff0df6307e886b7ec9bd89205f9e098105f61b`。PR #2 已合并，main 在 `06606890b5de3fc3fc234ef526de8afcaa716f21`。
- 主线 push CI 运行 `43862051668` 成功。Web 产物 `14224535495`，Server 产物 `14223786726`。已验证来源、归档摘要及组件。部署配置只用于渲染本地忽略的 Server 包。
- 迁移通过 `scripts/bootstrap-deployment.ps1` 和 `scripts/deploy-probe.ps1 -MigrateWeb` 完成，控制器已返回协议 3，静态内容为 legacy。线上既有后端仍是 `89ec4061eaa084a61e106022f68839fde3ca512d`。
- 前序后端操作 `calendar-release-20261115T114013Z-04fa8431` 已完成，保留 BLS 数据源限制。尝试 `calendar-release-20261116T121829Z-b1922d22` 时，控制器在登记前拒绝旧基线。查询原操作 ID 返回 `not_found`。旧基线记录的应用目录与已安装版本不同。
- 当前只读基线 `persistence-check-20261116T122017Z-68fee741` 已进入观察，预计 2026-11-16T13:25:20Z 结束。用 `scripts/verify-baseline.ps1 -Report -RunId` 查询本次操作，审核完成报告的摘要后发布后端。实际安装后应观察新闻采集与资源，再验证新 API 和独立前端。尚未有新前端发布记录。
- 本地阶段证据在忽略目录 `.validation/prod-43862051668/`；包括 `before-https.json`、`takeover-https.json` 和已校验包。可用 CI 编号重新下载通用包；生产配置和凭据仅留在既有部署环境。

## Verification

迁移命令的上传、切换退出码均为 0。公网对比入口、Worker 及预缓存共 10 项响应，接管前后 SHA-256 全部相同。健康、匿名会话、TrendRadar 入口和别名跳转均通过。维护者在原手机 PWA 确认已有登录和通知目标页均正常。桌面工具无法可靠识别已安装 PWA 的浏览器 URL，未另行计为桌面端验收。

## References

- PR #2；主线发布提交 `06606890b5de3fc3fc234ef526de8afcaa716f21`；CI `43862051668`。
- [迁移及发布入口](../../../../README.md)、[安装与接管](../../../../deploy/install.py)、[基线绑定规则](../../../../deploy/persistence_baseline.py)、[受限控制器](../../../../deploy/release_control.py)。

## Observations

- [result] legacy 静态目录已在生产使用，旧 PWA 的会话和通知跳转已获设备验收。
- [decision] 新设置页使用新增接口，先部署后端，再切换新前端。
- [blocker] 先前基线与当前服务版本不同，控制器已拒绝将其用于新后端。
- [next_step] 跟进 `persistence-check-20261116T122017Z-68fee741` 到完成并复核真实报告摘要。
