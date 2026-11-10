---
title: Codex checkpoint - 2026-11-09T16-16-21Z - Actions部署第二批调研后暂停
type: coding_session
permalink: finevent/codex/checkpoints/codex-checkpoint-2026-11-09-t16-16-21-z-actions-部署第二批调研后暂停
status: open
project: finevent
repository: finevent
branch: codex/actions-deploy-report
git_sha: b89a18e0357bfd7f4af57486fbf63bf79e0a7c5d
started: '2026-11-09T16:16:21Z'
capture: deliberate
agent: codex
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-09T16-16-21Z - Actions部署第二批调研后暂停

## Summary

通用产物构建与 Quality 门禁已合入 main；Actions 手动部署与报告查询仍处于调研阶段，用户因重启电脑明确要求暂停。

## Story

本会话从工程基线恢复，先完成 Router 引入并保留通知、旧深链接及前进后退行为，Pinia 按用户决定暂缓。随后将部署扩展分两批进行：第一批完成通用构建、环境渲染、真实 PR 验证和 Quality 门禁，同时保留本地构建发布入口。用户已手动合并第一批 PR，本地 main 与远端 main 核对到当前提交。

用户同意开始第二批：服务器成为发布操作记录的权威来源，本地与 Actions 复用部署逻辑；专用 SSH 密钥及严格主机指纹校验；手动 Deploy 选取通过 main CI 的固定产物，手动 Report 按操作 ID 查询；保留前序验收、互斥、SQLite 备份、失败回退及后台长时观察。先完成脚本、失败路径验证与只读预检，再做真实部署验收。发起后台观察不能等同验收通过；真机推送仍需实际设备验证。

调查发现当前私有部署配置使用 root，默认 SSH 私钥文件均不存在，ssh-agent 停止。用户明确答复“没有，准备一次性初始化脚本”，即没有其他密钥或登录会话；应让用户在自己的本机终端输入现有 SSH 密码执行一次性安装，不在聊天中索取密码。GitHub 当时尚无 production environment 和 repository Secrets。本轮没有连接或修改生产服务器。

用户随后要求参考 bubble-star-game。已只读检查相邻仓库的 Actions、infra/deploy/deploy.sh、server-bootstrap.sh、remote-apply.sh 及测试、README：可以借鉴 CI run/SHA 绑定产物、临时 SSH 凭据文件、BatchMode/IdentitiesOnly/严格 known_hosts、生产串行且不取消在途部署，以及初始化/上传/服务器校验切换的分层。Bubble 是纯静态站点，其无 root 的目录切换不覆盖 finevent 的 SQLite、systemd 和长时验收，不能照搬；也不能复用其密钥或真实部署配置。finevent 继续使用已确认的手动 Deploy/Report，不改为 Bubble 的自动 workflow_run 发布。

暂停前最后提出的实现方向是专用部署密钥限制为只能调用固定服务器发布控制器，复用现有观察与回退。此方向尚未落地，也没有创建密钥、上传 Secrets 或安装控制器。用户此时要求“先暂停，我要重启电脑了”，所有实施工作已经停止。

## Working State

- 当前本地分支 `codex/actions-deploy-report`，HEAD 为 `b89a18e0357bfd7f4af57486fbf63bf79e0a7c5d`；新建分支尚无 PR。暂停前 git status 干净，尚未编辑第二批代码。此 checkpoint 写入后会新增一个未跟踪的知识文件，恢复时不要误认为部署代码已开始。
- 第二批检查基点就是上述 SHA。尚未运行第二批测试，也没有待完成的构建、部署进程或 SSH 会话。
- `.deployment.local.json`、`.validation/` 与 `release/` 为机器本地忽略文件；不要提交、打印或上传真实地址、路径、密钥和完整服务器报告。当前本机旧指针 `.validation/calendar-release-latest.json` 指向 `calendar-release-…`、包 `finevent-20261102T143100Z.tar.gz`，profile 为 news；它不能证明当前服务器状态。
- 仓库无 `.codegraph/`，此次未使用 CodeGraph。Windows 命令用 PowerShell `login:false`，登录 profile 的 fnm 会失败。Git 写入和网络访问通常需 sandbox escalation；权限审查不等于需要重复向用户请求已授权步骤。
- 长输出继续使用 tool-output 技能的 CLI，保存完整日志到 `.validation/actions-deploy/logs`，只返回有界片段。不要主动创建子 agent。

恢复时重点核对以下现有实现，避免重写业务观察器：

1. `scripts/publish-calendar.ps1` 目前依赖本地 latest 指针和下载的前序报告，再上传包、在服务器启动 `deploy/calendar_release.py`。news/ai wrappers 共用它。应改为服务器权威记录，同时保留本地入口和前序参数。操作 ID 重试应返回同一操作，不能因 SSH 响应丢失再次安装。
2. `deploy/calendar_release.py` 已有服务器全局锁及 transition 锁、前序完整验收与源站例外精确报告摘要、后台 systemd unit、备份验证、观察和自动回退。Report 只是查询，不得再部署或调用模型。注册、启动、响应丢失之间的状态缺口必须保留为待恢复，不能猜测成功或盲目重启。
3. `scripts/inspect-calendar-observation.ps1` 使用本地 release 指针和完整 report.json。迁移报告协议时保留本地诊断兼容；Actions 只能输出脱敏摘要，不能把服务器真实路径、回退命令等完整报告公开为 artifact。
4. `scripts/build-release.ps1`、`render-release.ps1`、`release-archive.ps1`、`check-release.py` 已实现 generic/deployment 两类包及全文件校验。重新渲染同一通用包的 tar 时间戳可能变化，因此幂等校验应绑定稳定内容清单或等价身份，而不能只比较重新生成的压缩包字节摘要。
5. `deploy/ai_release.py` 的 AI profile 专用于首次 AI 初始化：preflight 明确要求服务尚未配置 AI，stage_key 需要交互式隐藏终端输入。不能把它当作普通非交互升级 profile 接入 Actions；保留单独交互流程，避免破坏现有本地入口。
6. `.github/workflows/ci.yml` 已产出 `finevent-linux-amd64-${{ github.sha }}` 通用 artifact，保留 14 天；新 Deploy 必须核对 CI workflow、同仓库 main push 成功、SHA、artifact 来源与摘要、release 元数据及干净构建。production environment 的 main 限制和临时 SSH 文件清理尚未配置。
7. README 的第二批 TODO 仍有效；完成实现与验收后更新，不提前删除待办。收尾按 project-knowledge 运行 `pnpm knowledge:check --since b89a18e0357bfd7f4af57486fbf63bf79e0a7c5d`。

## Verification

第一批已实际完成格式、Lint、61 个单测、Go test/vet、211 个 Python 部署测试、17 个 PowerShell 配置入口检查、30 个 Chromium 冒烟，以及真实 generic 构建和本地渲染。真实 PR CI 曾暴露 Linux 隐藏 .vite 文件漏包，已修复并验证 Quality 失败时阻塞 PR；最终 PR CI 通过，用户完成合并。main 的 CI 运行 42648888775 与 42648876756 在本次对话前段已核对成功。

第二批目前只有只读检查与分支创建，没有实现测试或生产验证。暂停前再次核对 git status、分支和 HEAD；GitHub 查询确认当前分支没有 PR，并可解析当前提交。该 checkpoint 是交接资料，不代表第二批已完成。

## References

- [finevent 仓库](https://github.com/Liranius/finevent)
- 已合入的第一批提交（旧提交 b89a18e0357bfd7f4af57486fbf63bf79e0a7c5d）
- 第一批 PR：前段会话已核对用户手动合并 PR #1。
- 参考项目 `bubble-star-game`，只读参考，不修改该仓库。
- SSH forced-command/restrict 语义已核对 [OpenBSD sshd 文档](https://man.openbsd.org/sshd)；environment 分支与 Secrets 限制核对 [GitHub 部署环境文档](https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments)。

## Observations

- [decision] 保持两批推进；第一批已合并，第二批使用手动 Deploy/Report，继续保留本地构建发布与现有验收规则。
- [decision] 用户已授权准备一次性 SSH 初始化脚本，并由用户在本机终端输入已有密码；不需要再询问是否另有 SSH 密钥。
- [blocker] 当前没有可用 SSH 密钥或登录会话，生产预检和部署需等待一次性初始化；脚本和离线测试可以先完成。
- [result] 第二批仅创建本地分支并完成参考项目调研，没有修改部署代码或生产状态。
- [next_step] 等用户重启后明确恢复，先核对工作区，再实现一次性初始化脚本和共用服务器发布控制器及失败路径测试。

## Relations

- relates_to [[分离通用构建与环境渲染，分批接入 GitHub Actions]]
