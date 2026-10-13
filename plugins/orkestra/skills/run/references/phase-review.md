# Phase: review

Goal: an independent, adversarial look at the whole change, with every finding triaged by you.

1. In ONE message dispatch in parallel (**XS/S: reviewer only** — no adversary), then end your turn (both reports arrive as messages):
   - `orkestra:reviewer` — `scope: final` (writes `reviews/final-review.md`)
   - `orkestra:adversary` (writes `reviews/adversary.md`) — **M/L only**
   (M/L touching auth/payments/data: also ask the researcher to double-check any platform/API claim the reviewers question.)
2. Triage — you verify each finding yourself by reading the cited code (reviewer severities are advisory). Write `reviews/triage.md` (`orkestra template triage`): every finding gets `fix | defer | reject` and a note — including the minors carried from task reviews
   (`orkestra task list` prints them). `reject` needs a reason; `defer` goes to the report's "Not done / deferred" section (Turkish report: "Yapılmayanlar / ertelenenler").
3. `fix` items → for each: `orkestra task add FIX-R001 "<short title>" -p <project>` (add `--no-tdd` only for non-testable
   fixes) → `orkestra task start FIX-R001` → implementer with a brief = finding + file:line + expected behavior + the test
   to add (red → green under that task id) → `orkestra task done FIX-R001 "triage R-001"`. Then `orkestra gate verify`
   again and mark the triage rows `done`.
4. `orkestra review check -p <project>` → OK, then `orkestra phase next`.
