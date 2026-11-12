---
title: Codex checkpoint - 2026-11-12T14-03-46Z - 报道列表双端时间排序验收
type: coding_session
permalink: finevent/codex/finevent/codex-checkpoint-2026-11-12-t14-03-46-z-报道列表双端时间排序验收
status: open
project: finevent
started: '2026-11-12T14:03:46Z'
capture: deliberate
agent: codex
repository: finevent
branch: codex/ui-refactor
git_sha: d714d24dc27a975c03ed4cc9ab21c894119bd092
tags:
  - codex
  - checkpoint
---

# Codex checkpoint - 2026-11-12T14-03-46Z - 报道列表双端时间排序验收

## Summary

报道列表现在用当前排序的北京时间同时决定日期分组和行内时刻。桌面和手机已用同一组跨日、同日和发布时间未知的样例验收。

## Story

这轮 UI 重构把阅读与配置分开，默认从报道列表进入。设置入口和刷新行为完成后，维护者确认了报道列表该显示哪些信息。日期和时刻取自同一字段，读者从高级搜索切换排序时，同一篇报道可以合理地移动到另一日期。接口继续负责排序与分页，列表只按返回顺序分组，详情保留精确到秒的时间和分类依据。

桌面在来源旁补充另一时间，跨日带日期，最多显示两个提及行业。手机按首次记录排序时仅在报道发表于更早日期时补短注。两端只标记最高规则重要性。TrendRadar 保留来源署名，上游周期在详情和来源说明中解释。完整约定见 `DESIGN.md`。

## Working State

分支 `codex/ui-refactor` 的本地合并提交为 `d714d24dc27a975c03ed4cc9ab21c894119bd092`，包含 `origin/main` 的 `e855716e4f6d5e33d7ffe53632ea2452c3405064`。核查时该分支没有 PR。报道列表修改与此前重构仍在未提交工作区，交接需要保留现有改动。忽略目录 `.validation/news-list-times/` 中的四张浏览器截图属于本地验收材料，可通过同组端到端用例重新生成。

## Changed Files

- `DESIGN.md` 定稿报道列表的时间与标签约定。
- `apps/web/src/news.ts` 统一北京时间的日期、主次时刻和跨日短注。
- `apps/web/src/components/NewsWorkspace.vue` 与 `apps/web/src/assets/news.css` 实现两端分组、行内信息和阅读入口。
- `apps/web/src/components/__tests__/news.spec.ts` 和 `e2e/news.spec.ts` 覆盖时间语义及双端阅读流程。

## Verification

- `pnpm ci:format`、`pnpm ci:lint`、`pnpm build`、`git diff --check` 通过。单测 `pnpm test:unit` 通过 75 项。
- `pnpm test:e2e` 的 Chromium 全量回归通过 47 项。最终版新用例单独复跑，桌面键盘和手机触屏模拟均通过。两端核对了高级搜索应用后的焦点、排序切换与撤销、详情返回，以及列表末尾的未知发布时间。
- 同一条报道在北京时间 11 月 11 日 23:58 发布，12 日 00:04 首次记录。按首次记录时归入 12 日，按来源发布时归入 11 日。发布时间未知的报道在第二种排序下单独排末尾。单测另覆盖跨年与午夜。
- `pnpm knowledge:check --since d714d24dc27a975c03ed4cc9ab21c894119bd092` 已核对关联知识。列表展示规则的维护入口是 `DESIGN.md`。

## References

- 主线基线 `origin/main@e855716e4f6d5e33d7ffe53632ea2452c3405064`，本地分支基线 `codex/ui-refactor@d714d24dc27a975c03ed4cc9ab21c894119bd092`。
- 新闻接口见 `docs/news-api.md`。分页排序实现见 `apps/server/internal/probe/news_store.go`。

## Observations

- [result] 同一 UTC 日期内跨北京时间午夜的样例，能区分按本地日历分组与按 UTC 日期分组。缺失发布时间用单独分组处理。
- [decision] 两端共用排序、分组和主时间语义。桌面允许并列对照，手机用跨日短注解释旧报道为何今天出现。
- [next_step] 维护者对照桌面和手机截图验收报道列表的最终展示。

## Relations

- continues [[Codex checkpoint - 2026-11-12T12-42-19Z - 设置首页记录入口与合并基线]]
