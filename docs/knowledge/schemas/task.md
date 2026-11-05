---
title: Task
type: schema
entity: Task
version: 1
schema:
  description: string, what needs to be done
  status?(enum, current state):
    - active
    - blocked
    - done
    - abandoned
  assigned_to?: string, who is working on this
  steps?(array): string, ordered steps to complete
  current_step?: integer, which step number we're on (1-indexed)
  context?: string, key context needed to resume after memory loss
  started?: string, when work began
  completed?: string, when work finished
  blockers?(array): string, what's preventing progress
  parent_task?: Task, parent task if this is a subtask
settings:
  validation: warn
permalink: finevent/schemas/task
---

<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Source: Basic Machines / Basic Memory, https://github.com/basicmachines-co/basic-memory/blob/1d0e09b6f4876c3f339c30e8dbfcf66e59e2c356/plugins/codex/schemas/task.md
Modified for Liranius/vue-baseline on 2026-10-29: added the project permalink and normalized formatting.
2026-10-30: added this provenance and modification notice; schema semantics and original prose are unchanged.
2026-11-05: adapted the project permalink for finevent; schema semantics are unchanged.
License: LICENSE in this directory. See ../../../THIRD_PARTY_NOTICES.md for scope.
-->

# Task

A **Task** is work-in-progress tracked as a note, so it survives context
compaction and shows up in the next session's brief. This schema is the same one
the framework-agnostic [`memory-tasks`](https://github.com/basicmachines-co/basic-memory/tree/main/skills/memory-tasks)
skill defines — kept identical here so the plugin and the skill agree on the
shape. For the full task workflow (creating, updating, completing), use that
skill.

Tasks are found by the SessionStart hook via structured recall:
`search_notes(metadata_filters={"type": "task", "status": "active"})`.

## Frontmatter vs observations

Put queryable fields (`status`, `priority`, `current_step`) in frontmatter so
`metadata_filters` can find them, and mirror them as `- [status] active`
observations so `schema_validate` sees them. `note_type="Task"` is stored as
lowercase `task` in frontmatter, so search with `note_types=["task"]`.
Validation is `warn` — advisory, never blocking.
