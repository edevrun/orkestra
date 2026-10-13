# Review triage: {{run_id}}

> The orchestrator verifies EVERY finding itself (reviewer severities are advisory) and records a verdict.
> verdict: fix | defer | reject · status: open | done · `fix` rows must reach `done` before delivery.

| id | source | finding (file:line) | verdict | status | note |
|---|---|---|---|---|---|
| R-001 | reviewer | `src/a.ts:42` missing null check | fix | done | closed by the T002 fix |
| R-002 | adversary | ... | reject | - | false positive: ... |
