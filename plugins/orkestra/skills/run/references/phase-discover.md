# Phase: discover

Goal: the facts needed to plan, with file paths — cheaply. Output: `context.md` (≤ 1500 tokens) + gate baseline.

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
   - `orkestra gate baseline -p <project>` (it may take minutes — that is the price of "never break what works"). Pre-existing failures are recorded, not blamed on this run; mention them in context.md.
5. Write `context.md` (format: `orkestra template context`) by merging the scout notes — facts and paths only, no design.
6. `orkestra phase next`.
