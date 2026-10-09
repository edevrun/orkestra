# UI flow audit (on demand — behavioural click-path audit; distilled from vendor/ecc: click-path-audit)

Load when the diff changes handlers, shared stores (Zustand/Redux/context) or effects behind a user-facing control — or
when a control "does nothing" although nothing crashes and every function works in isolation.

For each touched control, trace: handler → every state write, in order → effects those writes trigger → final UI state.
The final state must match what the control's label promises.

## Patterns to hunt
1. **Sequential undo** — call B resets what call A just set (a store action with a side-effect reset).
2. **Async race** — two async paths write the same state and the slower one overwrites; double submit.
3. **Stale closure** — the handler reads a value captured before the update.
4. **Missing transition** — a success or failure path never clears loading, closes the modal or re-enables the button.
5. **Conditional dead path** — a guard that can never be true (or always returns early) for this entry point.
6. **Effect interference** — a `useEffect` reacts to the new state and reverts or overrides it.

## Scope
- A shared store action changed → check ALL of its callers, not only the one in the task.
- Styling, performance and API-shape bugs are out of scope here (use the debugging playbook / stack checklists).

## Output
Per finding: control (file:line) · the write sequence that breaks it · expected vs actual final state · a test idea
(render → click → assert the final visible state). Untraced controls are reported as "not traced", never as OK.
