---
title: 分离通用构建与环境渲染，分批接入 GitHub Actions
type: decision
permalink: finevent/codex/decisions/分离通用构建与环境渲染分批接入-git-hub-actions
status: accepted
decided: '2026-11-09'
project: finevent
repository: finevent
sources:
  - README.md
  - .github/workflows/ci.yml
  - scripts/build-release.ps1
  - scripts/render-release.ps1
  - scripts/release-archive.ps1
  - scripts/check-release.py
  - scripts/deployment-config.ps1
  - scripts/test-release.ps1
  - deploy/install.py
  - deploy/test_release_bundle.py
  - tests/ci.test.ts
  - .github/workflows/deploy.yml
  - .github/workflows/report.yml
  - scripts/actions-release.py
  - scripts/bootstrap-deployment.ps1
  - scripts/release-client.py
  - scripts/publish-calendar.ps1
  - scripts/publish-ai-initial.ps1
  - deploy/bootstrap_release.py
  - deploy/release_control.py
  - deploy/test_actions_release.py
  - deploy/test_release_control.py
  - apps/web/scripts/precache.mjs
  - tests/precache.test.ts
  - e2e/pwa-update.spec.ts
---

# 分离通用构建与环境渲染，分批接入 GitHub Actions

原构建脚本同时编译应用和读取本机部署配置，发布又依赖交互式 SSH 及本地验收指针。直接把这些步骤搬进 Actions 会把私有环境值带入产物，也无法可靠衔接已有验收。用户确认分两批实施：第一批完成通用构建、真实 PR 验证和 Quality 门禁，第二批再接入手动生产部署与报告查询。

构建产出不含私有部署配置的 Linux amd64 通用包，包含 Go 程序、前端文件、部署模板和源码身份。环境渲染单独执行，只修改部署文件并重建摘要，保留应用内容及原通用包摘要。默认本地构建仍调用渲染，现有发布命令继续接受部署包；通用包必须先渲染，旧格式部署包保持兼容。命令与校验细节以 README 和脚本为准。

CI 对真实 PR 的合并结果执行完整检查，通用包构建、渲染回归和上传也属于 Quality 的必要步骤。GitHub 主分支要求来自 GitHub Actions 的 Quality 成功及分支同步，管理员同样遵守；合并方式采用 rebase。个人仓库不额外要求他人审批。平台实际状态与验收证据需从仓库和 PR 核对，不能仅凭此决策或本地测试推定门禁已经生效。

采用共用脚本能同时支持本地和 Actions，避免维护两套构建流程；以明确源码身份和摘要关联产物，便于下载后在本地发布。代价是多一次显式渲染，通用包与部署包需分别保存。没有选择直接上传本地私有发布包，也没有在第一批开启推送即生产部署。

PWA 的离线资源清单变化本身不会触发 Worker 更新。通用构建将资源内容版本写入 Worker 脚本，既使浏览器能发现新包，又保持相同内容的重复构建一致；更新确认和离线加载通过真实浏览器回归核对；换版夹具为旧、新文件设置不同的 HTTP 修改时间，避免同秒写入被条件请求误判为未修改，同时保留更新按钮及离线新版断言。说明和平台依据见 README。

用户随后授权推进第二批，并确认没有现成 SSH 密钥或登录会话，由本机终端输入现有密码执行一次性初始化。服务器操作记录成为权威来源，本地指针仅作缓存；本地和 Actions 复用控制器、前序验收及锁。专用 SSH 密钥限制为固定发布命令，仍具有运行安装代码所需的 root 发布权限。首次装机与 AI 密钥初始化保持独立交互入口。

Deploy 只选取同仓库 main push CI 的成功产物，核对 GitHub artifact 来源和摘要、包摘要及干净提交身份。操作 ID 绑定内容和前序参数，Actions 重新运行沿用同一 ID；响应丢失时查询既有操作，结果不明不自动重新安装。私有环境仅在临时渲染和服务器内使用，公开 Report 保留状态摘要，完整报告由本地入口下载到忽略目录。

第二批继续复用服务器备份、回滚和后台观察，不能把任务启动当成验收通过。日历完整观察长于 [GitHub 托管单个 job 上限](https://docs.github.com/en/actions/reference/limits)。操作入口由 README 的“SSH 初始化与 Actions 部署”维护，实际结果与验收边界见实施状态文档；本记录不重复维护执行状态。

## Observations

- [decision] 用户批准先实施通用构建、真实 PR 验证和 Quality 门禁，并保留本地构建发布入口。
- [decision] 私有环境配置只在渲染阶段使用，公开 Actions 仅保存通用包和摘要。
- [decision] 第一批不连接生产部署；生产自动化属于第二批。
- [decision] 第二批由用户手动发起部署和报告查询；无现成 SSH 凭据时先准备一次性初始化脚本。
- [constraint] 控制器记录与原有完整前序验收共同约束部署；删除本地指针或重新运行 job 不能绕过服务器状态。
- [constraint] 日历完整观察长于 GitHub 托管单个 job 上限，应继续由服务器后台任务执行。
- [branch] codex/actions-release-build
- [pr] 重建前 PR #1（原仓库 ID `1447032376`）；编号不对应新仓库的同号 PR，重建决定见 [[删除重建 GitHub 仓库并恢复受保护的发布流程]]。

## Relations

- follows [[Codex checkpoint - 2026-11-09T13-11-02Z - finevent工程基线与待验收事项]]
- extended_by [[通过只读复验更新部署基线，显式恢复安装前失败]]
