---
feature: {{run_id}}
risk: low
baseline_head: {{head}}
approved_dependencies: []
---
# Plan: {{title}}

> HOW. After the user approves, `orkestra` loads the tasks into the run state.
> After approval, every deviation from this file is written to the changelog at the bottom and shown as a diff in the delivery report.

## Approach
- Chosen: <architectural approach, 3-5 sentences>
- Alternatives considered: <A: why not>, <B: why not>

## Code map
| File | Change | Why / existing pattern to follow |
|---|---|---|
| `src/...` | modified | follow the pattern in `src/similar.ts:40-80` |

## Interfaces and contracts
<New/changed function signatures, API shapes, types, events. Who consumes and who produces them.>

## Global constraints
- The existing public API/behavior is not broken (baseline gates stay green).
- No unrequested refactors, formatting changes or dependencies.
- Code comments in English; user-facing text in the project's language.

## Tasks

### T001: <short title>
- Files: `src/a.ts` (modified), `src/a.test.ts` (new)   ← every file that does not exist yet is marked (new); the CLI checks this
- Depends: none
- Acceptance: Given <state> When <action> Then <outcome> (FR-001)
- Test: `<narrow command that runs a single test>`
- TDD: required

### T002: <short title>
- Files: `...`
- Depends: T001
- Acceptance: ... (FR-002, E1)
- Test: `...`
- TDD: required

## Test strategy and traceability
| Requirement | Test | Type |
|---|---|---|
| FR-001 | `a.test.ts › ...` | unit |
| SC-001 | `...` | integration/e2e/manual-UI |

## Verification gates
- Every gate that passed at baseline keeps passing (`orkestra gate verify`).
- Extra: <UI verification / manual scenario / performance measurement, if any>

## Risks and rollback
- Risk: <...> → Mitigation: <...>
- Rollback: `/orkestra:rollback` (file backups; no git writes)

## Exceptions
<Normally NONE. Only when the user explicitly asks: a file that needs a test deletion/skip or a lint/type suppression: `path` + reason.
This section is read from the approved plan; an exception added after approval is invalid.>

## New dependencies
<"None" if there are none. Otherwise for each: package, why, alternative, license. Also add it to approved_dependencies in the frontmatter; it takes effect with the user's approval.>

## Plan changelog
<!-- append-only: - YYYY-MM-DD HH:MM · what changed · why -->
