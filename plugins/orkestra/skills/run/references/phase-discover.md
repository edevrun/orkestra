# Phase: discover

Goal: the facts needed to plan, with file paths — cheaply. Output: `context.md` (≤ 1500 tokens); the gate baseline runs in the background.

1. Memory first (already in `orkestra context`): PROJECT.md summary, recent features, learnings. Open `memory/specs/<capability>.md` only for capabilities this request touches.
2. Dispatch scouts **in one message** (parallel), then end your turn — their notes come back as messages. Sizing: **XS → 0 scouts** (read 1–3 files yourself), **S → 0–1 scout** (prefer writing context yourself when the paths are obvious), M → 2, L → 3. Full scout teams are for M/L only. Each prompt:
   ```
   project: <abs path>
   focus: <e.g. "settings screen + theme provider" | "API route + DB model for orders" | "tests and fixtures for X">
   request: <one line>
   out: <run folder>/scout-<focus-slug>.md
   ```
3. If the change touches third-party APIs, SDKs, platform/store rules or a library upgrade → dispatch `orkestra:researcher` in the same parallel batch with specific questions (installed versions first).
4. Gates:
   - `orkestra gate list -p <project>`. If `confirmed: no` in context: sanity-check detected commands against manifests/AGENTS.md (e.g. pomi uses `dotnet test tests/Pomi.Engine.Tests`). Fix `.orkestra/config.json` if wrong, then `orkestra gate confirm`. In autopilot, select the strongest repository-backed gate; block only when no canonical command can be established safely.
   - `orkestra gate baseline -p <project>`: start it **in the same message as the scouts**, as a background shell command (it may take minutes; that is the price of "never break what works", and it overlaps the scouts, clarify and plan). Source cannot change before plan approval (the phase guard blocks it), so the baseline still describes the pre-change tree. It is no longer needed to leave discover or clarify; `phase next` into implement refuses until `gates-baseline.json` exists (a `gates-baseline.partial.json` means it is still running or was interrupted: wait or re-run it). Runs already past clarify are unaffected. Pre-existing failures are recorded, not blamed on this run; mention them in context.md. If the project has no gates it records an empty baseline (reported as a risk).
   - Later `orkestra gate verify` does not re-run a gate that failed or timed out at baseline (it is listed as "NOT protecting this run"); `gate verify --all` (or `ORKESTRA_GATES_FULL=1`) runs everything. After an interrupted verify, `gate verify --resume` runs only the gates not yet finished for the same tree state.
5. Write `context.md` (format: `orkestra template context`) by merging the scout notes — facts and paths only, no design.
6. `orkestra phase next`.
