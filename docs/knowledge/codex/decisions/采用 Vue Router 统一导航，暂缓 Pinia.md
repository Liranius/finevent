---
title: 采用 Vue Router 统一导航，暂缓 Pinia
type: decision
permalink: finevent/codex/decisions/采用-vue-router-统一导航暂缓-pinia
status: accepted
decided: '2026-11-09'
project: finevent
repository: finevent
sources:
  - README.md
  - DESIGN.md
  - apps/web/package.json
  - apps/web/src/router.ts
  - apps/web/src/main.ts
  - apps/web/src/App.vue
  - apps/web/src/workspace-navigation.ts
  - apps/web/src/components/CalendarWorkspace.vue
  - apps/web/src/components/NewsWorkspace.vue
  - apps/web/src/components/VerificationWorkspace.vue
  - apps/web/src/components/__tests__/router.spec.ts
  - e2e/navigation.spec.ts
  - docs/implementation-status.md
---

# 采用 Vue Router 统一导航，暂缓 Pinia

## 决定与背景

2026-11-09，用户确认先引入 Vue Router，Pinia 暂缓；保留现有通知和深链接，统一导航与前进后退，并完成相关验证。此前 App.vue、新闻和日历工作区分别处理 hash，新闻详情新增历史记录而日历详情替换记录，验证工作台没有独立地址。已有功能需要统一导航规则，因此 Router 的收益已经明确。

本次采用 `/finevent/` 下的 hash 路由，维持现有静态部署入口。旧通知、收藏和详情链接在路由入口兼容转换，组件按路由读取页面，具体地址和返回行为分别以 `apps/web/src/router.ts`、`apps/web/src/workspace-navigation.ts` 和 `DESIGN.md` 为准。验证工作台提取为路由组件，通知订阅与应用会话逻辑继续由 App.vue 管理。

## 取舍与后果

继续维护分散的手写路由可以省去依赖，但每新增入口仍需同步多个监听器及历史策略；这次选择由 Router 集中管理。HTML5 路径模式会增加静态服务及离线入口的适配范围，本次使用 hash 模式。

同时全面迁移 Pinia 未获采用：当前共享内容主要是会话和工作区偏好，仍可由父组件及 props/emits 管理。Pinia 的后续评估条件在 README 的 TODO 维护，不因组件拆分就把正文、表单和请求状态统一提升为全局缓存。

本决定仅确定导航与状态管理范围；后续提交、部署和 GitHub 门禁由用户另行授权，构建方案见 [[分离通用构建与环境渲染，分批接入 GitHub Actions]]。平台配置以 README 的 CI 章节为准，生产导航与真机通知的实际验收及边界见 `docs/implementation-status.md`。

## Observations

- [decision] 先采用 Vue Router 统一工作区、详情和设置导航，暂缓 Pinia。
- [rationale] 导航职责已分散且历史行为不一致，统一路由对现有业务有直接收益；共享状态规模尚不足以要求全面迁移 store。
- [alternative] 保留分散手写路由；同时全面引入 Pinia；改用 HTML5 路径模式。
- [consequence] 保留旧通知和深链接兼容；返回、刷新、焦点及迟到响应都属于导航验收范围。
- [branch] 实施从本地 main 的 `22fcc7e2cff001702e249f576fc771601561522d` 开始，保留既有暂存修改。

## Relations

- follows [[Codex checkpoint - 2026-11-09T13-11-02Z - finevent工程基线与待验收事项]]
