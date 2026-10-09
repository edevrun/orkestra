# Phase: intake

Goal: understand the request precisely enough to scope discovery. Output: a `## Sınıflandırma` section appended to `request.md`.

1. Restate the request in one sentence (Turkish). Identify the **outcome the user values**, not just the literal ask.
2. Classify (append to `.orkestra/features/<run>/request.md`):
   ```
   ## Sınıflandırma
   - Tür: feature | bugfix | refactor | perf | security | docs | infra | ui
   - Boyut: XS | S | M | L  (L → propose splitting into several runs; ask the user which part first)
   - Risk: low | medium | high  (data/migration, auth, payments, public API, store release → high)
   - Etkilenen yüzeyler: <web / iOS / Android / wear / API / CLI / DB ...>
   - Belirsizlikler (ilk izlenim): <bullets — candidates for clarify>
   ```
3. Red flags → stop and ask now (one AskUserQuestion), don't discover first:
   - the request spans **two projects** (isolation rule) → which one?
   - it asks for deploy/commit/store upload as part of the work → confirm it is wanted and when.
   - it contradicts an AGENTS.md invariant.
   - the target project is **orkestra itself** (the plugin that is running you) → read its
     `CONTRIBUTING.md` first: hook/guard/agent files must not change while any run is open in the workspace.
4. If `orkestra start` reported uncommitted changes, tell the user in one line that they are backed up and will not be attributed to this run.
5. `orkestra phase next`.
