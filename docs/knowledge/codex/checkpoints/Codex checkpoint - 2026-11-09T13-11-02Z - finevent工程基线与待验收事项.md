---
title: Codex checkpoint - 2026-11-09T13-11-02Z - finevent工程基线与待验收事项
type: coding_session
permalink: finevent/codex/checkpoints/codex-checkpoint-2026-11-09-t13-11-02-z-finevent-工程基线与待验收事项
status: open
project: finevent
repository: finevent
branch: main
git_sha: 22fcc7e2cff001702e249f576fc771601561522d
started: '2026-11-09T13:11:02Z'
capture: deliberate
agent: codex
sources:
  - .github/workflows/ci.yml
  - README.md
  - docs/implementation-status.md
  - docs/ai-api.md
  - docs/project-knowledge.md
  - .codex/basic-memory.json
  - apps/web/package.json
  - apps/web/src/main.ts
  - apps/web/src/App.vue
  - apps/web/src/workspace-navigation.ts
  - apps/web/src/components/NewsWorkspace.vue
  - apps/web/src/components/CalendarWorkspace.vue
  - DESIGN.md
  - apps/server/internal/calendar/repairs.go
  - deploy/trendradar_persist.py
  - e2e/workspace.spec.ts
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-09T13-11-02Z - finevent工程基线与待验收事项

## Summary

finevent 的工程基线和部署信息清理已在本地完成主要工作，下一阶段仍需讨论 Router/Pinia、构建方式与 GitHub 门禁，并按现有实施状态文档补齐生产验收。

## Story

本会话最初对比 finevent 的 Vue 子应用与 vue-baseline。用户完成前后端拆分后，要求先对齐 Router/Pinia 以外的工程能力。格式、Lint、类型检查、单测、浏览器测试、开发工具及相关工程配置已完成本地对齐；“基线对齐”只描述这些工程能力，不代表整体产品、生产验收或远端门禁已经完成。

随后发现仓库历史含部署信息。用户撤回相关远端提交并删除、重建远端仓库，明确要求连同公网 IP、SSH 目标、网站域名和部署路径一起处理。本地已将真实配置迁入忽略文件，跟踪文件采用示例值，发布时再渲染；已重写含这些值的历史并保留本地备份。这是对已知值和凭据特征的清理，不证明此前公开内容不存在外部留存。

用户后来要求先修正 CI 分支及 README，把 GitHub 实际验证留到 Router/Pinia 架构讨论完成后的构建方式讨论中。两处修改已完成并暂存，尚未提交。本次最新要求是补一份跨会话交接，因此本文保存现状与依据，不批准新的架构方案，也不替代各项待办的主记录。

关于未采用 Router/Pinia：本会话此前核对了仓库及早期聊天，未找到明确比较并否决这两个库的决策。早期目标是先验证推送、采集及时性和资源共存，再逐步扩展业务；“最初的验证实现随业务演进被沿用”是由此得出的推断，不是已确认的历史决策。当前可直接核对的事实是：应用未安装这两个依赖，App.vue 维护工作区和导航状态，通过 props/emits 与子组件交换状态，多个组件自行处理 hash 导航。没有证据表明用户禁止采用这些库，服务器内存约束也不能直接当作不采用浏览器端库的理由。

本会话对成熟度的评估是“从技术验证版演进而来的个人使用首版，仍有架构和验收收尾”：已有多批实际部署和签收，不能只按原型看待；也不能因单测通过或缺少某个库，就断言正式版条件已满足。这是评估结论，尚未形成新的发布标准或架构决定。

## Working State

记录时间为北京时间 2026-11-09 21:11。仓库根目录及 Git 身份见 frontmatter；当前分支为 `main`，本地 HEAD 为 `22fcc7e2cff001702e249f576fc771601561522d`。本次只读 GitHub 查询确认：远端 `main` 仍为 `6a2d1d07954fe8e303096ab8a3436baae9728d3b`，未发现以 `main` 为 head 的开放 PR。清理后的 HEAD 尚未推送，恢复时需重新核对远端，不沿用快照作为实时状态。

开始写本记录时，工作区只有已暂存的 `.github/workflows/ci.yml` 与 `README.md`，没有未暂存修改。这些暂存修改以及本次新增 checkpoint 都是当前机器上的未提交状态；同一工作区可以读取，其他工作区或机器需同步相应文件。不要在恢复时覆盖已有暂存内容。

`.deployment.local.json`、`.validation/history-cleanup/before.bundle` 和 `.validation/` 验证日志均被 Git 忽略。私有部署配置以及含旧历史的 bundle 只保留本地，不能作为可公开交接附件或重新推送的历史来源。审计摘要为 `.validation/history-cleanup/review.json`，提交映射为同目录 `commit-map.json`；它们可能随本机清理丢失，本文仅保存清理结果与边界，不复制真实部署值。

待办继续在下列主记录维护；本表是本次快照，关闭问题前须检查最新来源与实际证据。

| 范围             | 未决事项与解除条件                                                                                                                                                                       | 主记录                                                                                                        |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| 前端架构与构建   | TODO: 讨论导航、深链接、工作区切换和共享状态的职责，再决定 Router/Pinia 是否采用及迁移边界；随后讨论构建方式。尚无采用或永久放弃的决定。                                                 | `apps/web/src/App.vue`、`apps/web/src/workspace-navigation.ts`、新闻与日历工作区组件、`DESIGN.md` 的“行为”    |
| GitHub 门禁      | TODO: 按用户指定顺序，在构建方式讨论中完成真实 PR、Quality 必须通过、分支保护及仅允许 rebase merge 的平台配置与验证；本地配置不能代替远端结果。                                          | `README.md` 的“CI”                                                                                            |
| 重启与资源负载   | TODO: 生产带已保存关联及快照的重启前后核对、整机重启、持续自动解读与新闻推送等完整业务负载仍待验收；已有空数据重启和单次重叠调用不能替代。                                               | `docs/implementation-status.md` 的“仍待完成”                                                                  |
| 真机与真实业务   | TODO: 真机订阅超时/失败路径尚未单独复验；基金草稿、确认及跨设备读取等待真实公告补验。正常通知送达不关闭异常路径待办。                                                                    | 同上                                                                                                          |
| 来源质量与及时性 | TODO: 评估上游间歇故障及漏采影响，处理五分钟退避与 3–5 分钟目标的差距，补充可追溯延迟证据；BLS 403 及 HTML 跨月限制仍保留。                                                              | 同上，并按文内链接核对接口与快照说明                                                                          |
| AI 费用          | TODO: 补供应商账单核对未结预留的入口与节假日价格日历；取得账单和日历来源证据后才能释放未知预留或标为实际费用。                                                                           | `docs/ai-api.md` 的“月预算与价格”                                                                             |
| 兼容性债务       | TODO: 旧程序回退需求结束后移除日历一次性兼容重试；TrendRadar 升级需复核固定镜像补丁；固定 Playwright 版本的问题修复后恢复 WebKit 离线模拟。                                              | `apps/server/internal/calendar/repairs.go`、`deploy/trendradar_persist.py`、`e2e/workspace.spec.ts` 中的 TODO |
| 新部署配置       | TODO: 新配置加载与 SSH/SCP 包装已经本地和 mock 验证，尚未连接生产执行新包装；实际部署时补验证。本次没有发布或生产连接。                                                                  | `README.md` 的部署说明及本地 `.validation/history-cleanup/review.json`                                        |
| 跨会话知识       | TODO: 当前 MCP 已可调用且项目路径核对正确；独立新会话的精确恢复、SessionStart/PreCompact 的客户端信任和实际加载仍需验证，不能仅凭 MCP 可用关闭。中文召回和长期知识维护效果仍需真实试点。 | `docs/project-knowledge.md`                                                                                   |

## Changed Files

- `.github/workflows/ci.yml`：已暂存，将 push 触发分支从 master 修正为 main；PR 与手动入口保留。
- `README.md`：已暂存，同步 CI 分支、已配置远端的事实，以及“架构讨论后再讨论构建与验证 GitHub”的顺序。
- 本 checkpoint：新增于 `docs/knowledge/codex/checkpoints/`，用于显式恢复。此前该知识目录只有 schema，没有相关任务、决定或 checkpoint 笔记。

先前基线与脱敏实现已包含在本地 HEAD 中，不再逐文件复制变更清单；当前源码、Git diff 和原有文档仍是实现依据。

## Verification

以下是本会话前序实际执行的检查，本次创建交接记录没有重跑应用测试。恢复后如继续改代码，按当前 AGENTS.md 重新选择相应验证。

- CI/README 修正后：`pnpm exec oxfmt --check .github/workflows/ci.yml README.md`、`git diff --check`、YAML 解析及 main/PR/manual 触发断言通过；`pnpm ci:format`、`pnpm ci:lint`、`pnpm test:unit` 通过，48 个单测通过；`pnpm test:e2e` 通过，25 个 Chromium 测试通过并包含类型检查和构建。
- CI 任务日志保存在本机 `.validation/ci-main/logs/`。本次已复核成功 manifest 和测试计数。
- 历史清理最终复核：格式、Lint、前端单测、Go test/vet、204 个 Python 部署测试、Chromium 和提交信息检查通过；17 个入口的配置检查使用 mock，26 份部署文件经私有配置渲染后与原生产文本逐字节一致；扫描覆盖 7 个提交、281 个唯一 blob，未命中所核对的私有值或凭据特征。详见本地 `.validation/history-cleanup/review.json`，不把该扫描扩展为对外部副本或未知秘密的保证。
- 本次已实际调用 Basic Memory 项目列表与主题检索，确认 `finevent` 绑定当前工作区的 `docs/knowledge`；检索和本地字面回退未找到既有相关笔记。已只读核对 GitHub 仓库、远端分支及 main 的开放 PR 列表。沙箱内代理连接失败后，经受控权限调用完成只读核对。
- 尚未执行真实 GitHub PR 门禁验收、新包装的生产部署、独立新会话恢复，以及上表的生产业务验收。本文不将这些项目记为已完成。

## References

- [GitHub 仓库](https://github.com/Liranius/finevent)：本次通过 GitHub API 确认的规范地址。
- 当前远端 main 提交（旧提交 6a2d1d07954fe8e303096ab8a3436baae9728d3b）：本次经 API 验证存在；本地 HEAD 仍以代码形式记录，不构造尚未推送的提交链接。
- `README.md`、`.github/workflows/ci.yml`：工程流程与本次已暂存改动。
- `docs/implementation-status.md` 的“仍待完成”、`docs/ai-api.md`：业务验收与费用待办的主要依据。
- `docs/project-knowledge.md`、`.codex/basic-memory.json`：当前工作区的知识读写、同步及验收边界。
- `apps/web/package.json`、`apps/web/src/main.ts`、`apps/web/src/App.vue`、`apps/web/src/components/NewsWorkspace.vue`、`apps/web/src/components/CalendarWorkspace.vue`、`apps/web/src/workspace-navigation.ts`、`DESIGN.md`：导航与状态现状及已有交互约束。

## Observations

- [result] 工程基线与部署信息历史清理已在本地完成主要工作；本地 main 尚未推送，CI/README 修正仍为暂存未提交。
- [decision] 用户要求先处理 Router/Pinia 以外的工程对齐；Router/Pinia 保留为架构讨论，不预先批准迁移。
- [decision] 用户要求网站域名和部署路径也纳入清理，真实部署配置与旧历史备份仅本地保留。
- [decision] 用户明确将 GitHub 平台配置与实际验证放在架构讨论后的构建方式讨论中。
- [result] 未找到明确拒绝 Router/Pinia 的历史决策；验证实现逐步沿用只是基于历史的推断。
- [result] 已部署业务的批次签收不等于首版全部验收，长期负载、整机重启及真实业务路径等仍按实施状态文档跟进。
- [next_step] 在新会话用本 checkpoint 的精确标识恢复，重新核对工作区后继续 Router/Pinia 的职责、必要性和迁移边界讨论。
