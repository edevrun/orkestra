# Phase: clarify

Goal: remove every ambiguity that would change the result — and nothing else. Output: `spec.md` with `## Clarifications` (`## Netleştirmeler` when the conversation is Turkish).

Read `approval_mode` from `.orkestra/current/state.json` first. In `autopilot` (the default), do not call
`AskUserQuestion`, `orkestra await`, or otherwise wait for the user: infer conservative defaults from the request,
codebase and project memory, and record each material assumption in `spec.md`. In `manual`, ask only questions whose
answers materially change the outcome.

## 1. Find the real questions

Scan the request + context against this taxonomy and keep only items where (a) the code/memory/AGENTS.md cannot answer, and (b) different answers produce materially different code or UX:
scope boundaries · user roles/permissions · data model & persistence · UX flows, states (empty/loading/error) & copy language · edge cases · non-functional (performance, offline, accessibility, security) · integrations & versions · platforms/parity · success signal · rollout/compat with existing data.
Decide yourself (and note it under "Assumptions and open risks", Turkish: "Varsayımlar ve açık riskler") anything that follows from codebase conventions — the user should not answer what the code already says.

## 2. Ask efficiently (manual mode only)

- ≤ 4 questions per call, ≤ 2 rounds. Usually 0–4 questions total; XS often 0.
- Each question: concrete, one decision, 2–4 options; put the recommended option first with "(Recommended)" (Turkish: "(Önerilen)") and say in the description what it implies.
- Ask in the user's language, with short headers (≤ 12 chars).
- **Enrichment**: in the same call, offer valuable additions you would ship as a principal engineer (e.g. empty/error states, validation, a11y, analytics event, migration safety) as ONE `multiSelect` question titled "Extra scope" (Turkish: "Ek kapsam"). Only selected items enter scope.

## 3. Write spec.md

`orkestra template spec` → fill it for this run (in the user's language). Rules: FR/SC ids (`FR-001`, `SC-001`), testable statements, edge-case matrix, "Out of scope" explicit, every Q&A recorded under `## Clarifications / ### Session <date>` (Turkish: `## Netleştirmeler / ### Oturum <date>`); if no question was needed, write why.
For S/M/L you may delegate drafting to `orkestra:architect` (give it the answers and paths); for XS write it yourself (short).

## 4. `orkestra phase next`
