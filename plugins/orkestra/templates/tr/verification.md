# Verification — {{run_id}}

> Written by orkestra:verifier. Goal-backward: start from each requirement, find the test that proves it,
> confirm that test RAN in this session. Do not trust task reports.

## Gate comparison (from `orkestra gate verify`)
<paste the table>

## Traceability
| ID | Kanıt (doğrulanabilir referans ZORUNLU) | Sonuç |
|---|---|---|
| FR-001 | `src/a.test.ts::handles X` (dosya + test adı gerçekten var olmalı) | PASS |
| SC-001 | gate `test` (son verify PASS) + `evidence/SC-001-....log` (`orkestra evidence SC-001 -- <komut>`) | PASS |

Kurallar (CLI `orkestra trace check` ile zorlanır): var olmayan dosya/test adı = FAIL · "manual/elle" kontrol =
`orkestra evidence` logu olmadan geçersiz · her satırda en az bir doğrulanabilir referans.

## Edge-case matrix audit
| # | Test | Sonuç |
|---|---|---|
| E1 | ... | PASS |

## Dependency check
<output of `orkestra deps check`>

## Notes
<anything partial, skipped or unverifiable — say it plainly>

VERDICT: <PASS|FAIL>
