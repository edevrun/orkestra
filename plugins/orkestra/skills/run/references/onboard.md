# Onboarding (once per project)

Goal: compact, verified project memory so every later run starts informed and cheap.

0. Engines (host machine, not this playbook): if `orkestra doctor` shows no ready engine, the human runs `orkestra setup` (or `orkestra setup --yes`), or `orkestra cursor install` / `orkestra copilot install`. Do not invoke install/setup from an agent shell.
1. `orkestra init -p <project>` (creates `.orkestra/`, detects gates; `.orkestra/runs/` is git-ignored automatically).
2. Read the project's AGENTS.md / CLAUDE.md / README (head) yourself — they are the canonical rules.
3. Dispatch in parallel (one message) 2–3 `orkestra:scout` calls; `out` = `/tmp/orkestra-onboard-<project>/scout-<n>.md` (scratch, outside the repo):
   - focus "stack, entry points, structure, build/run commands"
   - focus "conventions (naming, state, errors, i18n, styling) and testing (framework, location, how to run one test)"
   - focus "integrations, data stores, env var names, deploy target, known fragile areas" (M/L projects)
4. Write `.orkestra/memory/PROJECT.md` (template already there): summary block first (it is what every run reads), ≤ 120 lines, every claim verified against the code.
5. Gates: verify the detected commands against package scripts / AGENTS.md; fix `.orkestra/config.json` if needed; run `orkestra gate run -p <project>` only if quick; `orkestra gate confirm`.
   Optional per-gate fields in `.orkestra/config.json` (all off by default):
   - `"idle_timeout": N` kills a gate that prints nothing for N seconds (recommended `300` for `kind: "e2e"`; leave it off for silent builds).
   - `"paths": ["src/**", "tests/**"]` lets `gate verify` carry a PASS forward while no file matching those globs changed (reported as carried; `gate verify --all` is required once before deliver).
   - `"parallel": true` / `"exclusive": true`: with `gates_parallel` > 1 (project `config.json` or `~/.orkestra/config.json`) typecheck/lint/test gates run concurrently up to min(N, cpus/2), then build, then e2e serially. Enable it when the baseline takes over 120 s and the gates do not share caches, ports or build directories.
6. Tell the user in 2–3 lines what was learned and which gates protect the project.
