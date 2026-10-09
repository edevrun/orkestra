# Debugging (systematic — distilled from vendor/superpowers/skills/systematic-debugging)

Use when a test/gate keeps failing after two attempts, or behavior is not understood. No fix before a root cause.

1. **Reproduce** — one exact command that shows the failure; capture the full error (not a summary).
2. **Read the evidence** — stack trace top frame in YOUR code, the actual vs expected values, recent changes (`orkestra changes`, `orkestra diff`).
3. **Locate** — trace the data backwards from the symptom to where it first becomes wrong. In multi-component flows log/inspect each boundary once instead of guessing.
4. **Compare** — find a similar code path that works (same repo) and list the differences.
5. **One hypothesis** — state it in one sentence; design the smallest experiment that would falsify it; run it.
6. **Fix the cause** — add/keep a failing test that captures the root cause, make one change, re-run the narrow test and the fast gates.
7. **Three strikes** — if three fixes failed, stop: the design is likely wrong. Write the finding in the task notes and escalate to the orchestrator / user with the evidence.

Never: weaken assertions, add sleeps/retries to hide races, catch-and-ignore, or skip the test.
