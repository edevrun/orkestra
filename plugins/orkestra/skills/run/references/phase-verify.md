# Phase: verify

Goal: fresh proof that (1) nothing that worked is broken and (2) every requirement is met.

1. `orkestra gate verify -p <project>`.
   - REGRESSION → `orkestra phase back implement --reason "<gate> regressed"`, brief an implementer with the failing tail (fix the cause, never the test), then return here.
2. Dispatch `orkestra:verifier` (project path + run folder). It reads the gate result with `orkestra gate last` (it does
   NOT re-run the full suite), runs only narrow tests it needs as evidence, and writes `verification.md` ending in `VERDICT: …`.
   End your turn after dispatching; its report resumes you.
3. FAIL → fix via implementer (back to implement) and repeat. Unverifiable items must be stated as such — never upgraded to PASS.
4. `orkestra phase next` re-checks deterministically: gates green after the last change, traceability with verifiable
   references only (no invented files/tests), test integrity (no removed tests, skip/only markers or new suppressions
   unless listed in the approved plan's `## İstisnalar`), and dependencies.
