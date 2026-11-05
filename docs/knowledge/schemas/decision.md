---
title: Decision
type: schema
entity: Decision
version: 1
schema:
  decision: string, the choice that was made
  rationale?: string, why this choice over the alternatives
  alternative?(array): string, options that were considered and not taken
  consequence?(array): string, what this decision commits us to
  context?: string, the situation that prompted the decision
  affects?(array): Entity, work or notes this decision bears on
  supersedes?: Entity, a prior decision this one replaces
settings:
  validation: warn
  frontmatter:
    status?(enum, lifecycle of the decision):
      - open
      - accepted
      - superseded
      - rejected
    decided?: string, when the decision was made (ISO timestamp)
    project?: string, the Basic Memory project this decision belongs to
permalink: finevent/schemas/decision
---

<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Source: Basic Machines / Basic Memory, https://github.com/basicmachines-co/basic-memory/blob/1d0e09b6f4876c3f339c30e8dbfcf66e59e2c356/plugins/codex/schemas/decision.md
Modified for Liranius/vue-baseline on 2026-10-29: added the project permalink and normalized formatting.
2026-10-30: added this provenance and modification notice; schema semantics and original prose are unchanged.
2026-11-05: adapted the project permalink for finevent; schema semantics are unchanged.
License: LICENSE in this directory. See ../../../THIRD_PARTY_NOTICES.md for scope.
-->

# Decision

A **DecisionNote** is a durable record of a real choice — one with alternatives
and a rationale, not a passing preference. Basic Memory host integrations
encourage agents to capture these as decisions are made or explicitly requested.

Decisions are found by structured recall:
`search_notes(metadata_filters={"type": "decision", "status": "open"})`.

## What makes a good DecisionNote

- **decision** — state the choice plainly.
- **rationale** + **alternative** — why this, and what was rejected. This is the
  part that saves a future session from relitigating the same ground.
- **consequence** — what the choice commits the work to.
- **affects** / **supersedes** — relations that wire the decision into the graph.

## Frontmatter

`type: decision` plus `status` make decisions queryable. Capture decisions
sparingly — one note per genuine decision, not per opinion. Validation is `warn`,
never blocking.
