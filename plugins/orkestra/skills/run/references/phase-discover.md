# Phase: discover

Goal: the facts needed to plan, with file paths — cheaply. Output: `context.md` (≤ 1500 tokens) + gate baseline.

1. Memory first (already in `orkestra context`): PROJECT.md summary, recent features, learnings. Open `memory/specs/<capability>.md` only for capabilities this request touches.
2. Dispatch scouts **in one message** (parallel), then end your turn — their notes come back as messages. Sizing: XS → 0–1 scout (you may read 1–3 files yourself), S → 1, M → 2, L → 3. Each prompt:
   ```
   project: <abs path>
   focus: <e.g. "settings screen + theme provider" | "API route + DB model for orders" | "tests and fixtures for X">
   request: <one line>
   out: <run folder>/scout-<focus-slug>.md
   ```
3. If the change touches third-party APIs, SDKs, platform/store rules or a library upgrade → dispatch `orkestra:researcher` in the same parallel batch with specific questions (installed versions first).
4. Gates:
   - `orkestra gate list -p <project>`. If `confirmed: no` in context: sanity-check the detected commands against package.json/AGENTS.md (e.g. pomi uses `dotnet test tests/Pomi.Engine.Tests`). Fix `.orkestra/config.json` if wrong, then `orkestra gate confirm`. Ask the user only if you cannot tell which command is canonical.
   - `orkestra gate baseline -p <project>` (it may take minutes — that is the price of "never break what works"). Pre-existing failures are recorded, not blamed on this run; mention them in context.md.
5. Write `context.md` (format: `orkestra template context`) by merging the scout notes — facts and paths only, no design.
6. `orkestra phase next`.
