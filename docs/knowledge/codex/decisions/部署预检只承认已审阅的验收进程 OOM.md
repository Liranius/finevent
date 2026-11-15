---
title: 部署预检只承认已审阅的验收进程 OOM
type: decision
permalink: finevent/codex/decisions/部署预检只承认已审阅的验收进程-oom
status: accepted
project: finevent
decided: '2026-11-15'
sources:
  - deploy/calendar_release.py
  - deploy/persistence_baseline.py
  - deploy/test_persistence_baseline.py
tags:
  - deploy
  - decision
---

# 部署预检只承认已审阅的验收进程 OOM

生产操作 `calendar-release-…` 的验收进程在 64 MiB 限额下被终止。原操作续验完成后，证据把主机累计计数的 19 → 20 归因于该事件，Finevent 和 TrendRadar 的计数均保持为零。以当次实时采样重建计数基线会掩盖后续故障。

- [decision] 部署预检和只读基线复验均核对原固化报告及该操作终态报告的 SHA-256，只承认这一次主机计数增量。
- [constraint] 重复运行使用同一个历史参考值。证据变动或缺失、计数增加或回退，以及任一业务服务的计数变化都会阻止验收。
