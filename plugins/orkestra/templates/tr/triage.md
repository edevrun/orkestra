# Review triage — {{run_id}}

> The orchestrator verifies EVERY finding itself (reviewer severities are advisory) and records a verdict.
> verdict: fix | defer | reject · status: open | done · `fix` rows must reach `done` before delivery.

| id | kaynak | bulgu (dosya:satır) | verdict | status | not |
|---|---|---|---|---|---|
| R-001 | reviewer | `src/a.ts:42` null check eksik | fix | done | T002 düzeltmesiyle kapandı |
| R-002 | adversary | ... | reject | — | yanlış pozitif: ... |
