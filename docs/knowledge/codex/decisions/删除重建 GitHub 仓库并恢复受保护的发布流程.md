---
title: 删除重建 GitHub 仓库并恢复受保护的发布流程
type: decision
permalink: finevent/codex/decisions/删除重建-git-hub-仓库并恢复受保护的发布流程
status: accepted
decided: '2026-11-11'
project: finevent
repository: finevent
sources:
  - README.md
  - docs/implementation-status.md
  - .github/workflows/ci.yml
  - .github/workflows/deploy.yml
  - .github/workflows/report.yml
tags:
  - github
  - privacy
  - deployment
---

# 删除重建 GitHub 仓库并恢复受保护的发布流程

用户确认通过删除并重建同名仓库清理远端历史，而不是临时放宽保护进行强推。先前 checkpoint 将实际部署地址重新带入公开历史；常规删除提交不会移除旧内容，改写分支也不能自行清除 GitHub 的旧 PR 内部引用。选择重建会丢弃旧远端 PR、Actions 运行及仓库配置，需要保留验收证据并重新配置门禁；它不承诺清除他人的克隆、fork 或平台留存副本。

重建前核对干净分支历史、备份仓库设置和关键运行证据，并验证本机部署凭据与现有受限 SSH 入口。GitHub CLI 缺少 delete_repo 权限，用户在页面完成删除后明确要求继续重建。同名公开仓库从 ID 1447032376 变为 1452907708；旧编号只属于旧仓库，不能因名称相同就视为新仓库的 PR 或检查证据。

新仓库先使用仅脱敏的已验收基线，再通过真实 PR 检查合入采集修复。恢复原有 Quality、管理员约束、rebase merge 和 production 仅限 main 的策略；环境 Secrets 从本机已有私有文件恢复，不能从 GitHub 备份取回其值。只读 Report 用于核对连接，重建仓库本身不触发应用部署或重启。

旧远端引用与包含未脱敏历史的本地工作分支不能整体重新推送。私有历史备份留在忽略目录，公开来源仅使用已扫描的提交；实际生产效果仍按原发布与验收流程处理。

## Observations

- [decision] 用户批准删除重建同名公开 GitHub 仓库，并在完成删除后授权继续恢复。
- [rationale] 以干净历史建立新的仓库身份，避免将旧 PR 引用带入新仓库。
- [consequence] 旧 PR 与运行编号失去当前仓库的引用意义，配置和验收证据需要分别恢复或留存。
- [constraint] 重建不绕过 Quality，也不把原生产验收扩展为采集修复的生产验收。

## Relations

- extends [[分离通用构建与环境渲染，分批接入 GitHub Actions]]
- follows [[Codex checkpoint - 2026-11-10T16-11-33Z - 采集重试已验证待选择远端清理方式]]
