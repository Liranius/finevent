---
title: Codex checkpoint - 2026-11-16T14-40-09Z - 生产前后端独立发布及公网验收
type: coding_session
permalink: finevent/codex/finevent/codex-checkpoint-2026-11-16-t14-40-09-z-生产前后端独立发布及公网验收
status: open
project: finevent
started: '2026-11-16T14:40:09Z'
capture: deliberate
agent: codex
repository: finevent
branch: codex/restore-server-install-check
git_sha: 2003bac4d1b2c779db204ca6c6320bc8e6f9b9b0
pull_request_number: '3'
pull_request_title: 'fix(deploy): 恢复独立后端安装时的设置服务检查'
pull_request_state: MERGED
pull_request_base: main
pull_request_head: codex/restore-server-install-check
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-16T14-40-09Z - 生产前后端独立发布及公网验收

## Summary

生产站点已由 Nginx 接管静态内容，独立后端和前端已发布到同一主线提交，两个阶段的真实服务器报告和公网核验均已完成。

## Story

前后端原先共用发布包。主线拆分后，先校验旧发布清单并把当前页面复制到 legacy 静态目录，再切换 Nginx。公网的 10 项响应在接管前后字节一致。维护者在原手机 PWA 确认登录状态和旧通知跳转正常。由于新版设置页面依赖新增后端接口，本次先发后端，再发前端。

前序基线绑定上一次安装前的版本，初次后端请求在登记前被拒绝。新的只读基线通过了 65 分钟观察。等待期间复核安装包，发现拆分时遗漏了 `check_settings_socket()` 定义。回归用例先复现缺失，再恢复原有只读探针，通过 Quality 后以 PR #3 合入主线。最终运行的两个包来自修复后的同一 main push。

## Working State

- `main` 生产提交为 `9fae67303089638b9939f5f3121ba72d5a53c607`。修复分支 `codex/restore-server-install-check` 指向 `2003bac4d1b2c779db204ca6c6320bc8e6f9b9b0`，两棵 Git 树一致。
- 正式 CI 运行 `43867398744`。Server 产物 `14226032613`，Web 产物 `14226112070`。均已核验来源、组件与摘要。
- 只读基线 `persistence-check-20261116T122017Z-68fee741` 完成，报告摘要 `7ddbc838e7ce069b2cf598fe26ac42dadb83bae14314092c0054d62a92679bed`。
- 后端操作 `calendar-release-20261116T132651Z-27924253` 为 `completed_for_review`。前端操作 `web-release-20261116T143413Z-c3a0b58f` 为 `completed`，当前静态指针与该 ID 一致，可回滚到 `legacy`。两份最终报告均无待恢复状态。
- 生产事实可通过 `scripts/release-client.py status` 查询上述操作。工作区忽略目录 `.validation/prod-43862051668/` 和 `.validation/prod-43867398744/` 保存本次公网摘要和操作结果。临时报表、归档和本机部署凭据均独立于跟踪的修复提交。

## Changed Files

[安装器](../../../../deploy/install.py) 恢复 Unix 套接字只读检查。[回归用例](../../../../deploy/test_trend_settings.py) 覆盖正常读取、服务错误及响应缺少来源列表。

## Verification

- 修复提交通过全量 Lint、75 项应用与工具单测、应用构建、提交快照格式检查和 262 项 Linux 部署测试。PR 的 Quality 与合并后主线 CI 均成功。
- 备份与重启验证通过。后端的 65 分钟新闻与资源观察在 2026-11-16T14:33:35Z 完成。771 次采样，主机可用内存最低约 202 MiB，OOM 增量为零。TrendRadar 完成 3 轮，两个新闻来源的轮询间断均为零。
- 启用新后端后旧前端的 10 项响应摘要仍与迁移前一致。切换新前端后，已从公网验证 10 个 CI 静态文件、Worker 作用域和 2 个旧哈希资源。健康、会话与别名跳转均通过。
- 旧手机 PWA 的登录和通知已由维护者确认。当前已请求新版手机设置页的读验证，尚无结果。桌面工具因不能可靠识别已安装 PWA 的当前 URL 而停止，未计入验收。

## References

- 拆分 PR #2，合并提交 `06606890b5de3fc3fc234ef526de8afcaa716f21`。安装修复 PR #3，合并提交 `9fae67303089638b9939f5f3121ba72d5a53c607`。
- [发布说明](../../../../README.md)、[静态发布](../../../../deploy/web_release.py)、[生产报告](../../../../scripts/release-client.py)。

## Observations

- [result] 前端独立切换时后端发布记录有效，旧资源仍可访问。
- [decision] 带新 API 依赖的页面先发布兼容后端，然后切换静态入口。
- [next_step] 有设备反馈时，补记新版手机 PWA 的 TrendRadar 来源列表加载结果。

## Relations

- continues [[Codex checkpoint - 2026-11-16T12-21-09Z - Nginx 接管已验收与当前生产基线复验]]
