# Verification: {{run_id}}

> Written by orkestra:verifier. Goal-backward: start from each requirement, find the test that proves it,
> confirm that test RAN in this session. Do not trust task reports.

## Gate comparison (from `orkestra gate verify`)
<paste the table>

## Traceability
| ID | Evidence (a verifiable reference is REQUIRED) | Result |
|---|---|---|
| FR-001 | `src/a.test.ts::handles X` (both the file and the test name must really exist) | PASS |
| SC-001 | gate `test` (last verify PASS) + `evidence/SC-001-....log` (`orkestra evidence SC-001 -- <command>`) | PASS |

Rules (enforced by the CLI with `orkestra trace check`): a file or test name that does not exist = FAIL · a "manual"
check is invalid without an `orkestra evidence` log · every row needs at least one verifiable reference.

## Edge-case matrix audit
| # | Test | Result |
|---|---|---|
| E1 | ... | PASS |

## Dependency check
<output of `orkestra deps check`>

## Notes
<anything partial, skipped or unverifiable: say it plainly>

VERDICT: <PASS|FAIL>
