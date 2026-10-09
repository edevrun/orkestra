# Phase: clarify

Goal: remove every ambiguity that would change the result — and nothing else. Output: `spec.md` with `## Netleştirmeler`.

## 1. Find the real questions
Scan the request + context against this taxonomy and keep only items where (a) the code/memory/AGENTS.md cannot answer, and (b) different answers produce materially different code or UX:
scope boundaries · user roles/permissions · data model & persistence · UX flows, states (empty/loading/error) & copy language · edge cases · non-functional (performance, offline, accessibility, security) · integrations & versions · platforms/parity · success signal · rollout/compat with existing data.
Decide yourself (and note it in "Varsayımlar") anything that follows from codebase conventions — the user should not answer what the code already says.

## 2. Ask efficiently (AskUserQuestion)
- ≤ 4 questions per call, ≤ 2 rounds. Usually 0–4 questions total; XS often 0.
- Each question: concrete, one decision, 2–4 options; put the recommended option first with "(Önerilen)" and say in the description what it implies.
- Turkish, short headers (≤ 12 chars).
- **Enrichment**: in the same call, offer valuable additions you would ship as a principal engineer (e.g. empty/error states, validation, a11y, analytics event, migration safety) as ONE `multiSelect` question titled "Ek kapsam". Only selected items enter scope.

## 3. Write spec.md
`orkestra template spec` → fill it for this run (Turkish). Rules: FR/SC ids (`FR-001`, `SC-001`), testable statements, edge-case matrix, "Kapsam dışı" explicit, every Q&A recorded under `## Netleştirmeler / ### Oturum <date>`; if no question was needed, write why.
For S/M/L you may delegate drafting to `orkestra:architect` (give it the answers and paths); for XS write it yourself (short).

## 4. `orkestra phase next`
