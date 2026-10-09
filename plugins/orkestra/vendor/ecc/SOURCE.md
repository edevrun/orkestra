# vendor/ecc — pinned, read-only references

| upstream | commit | date | license | used for |
|---|---|---|---|---|
| https://github.com/affaan-m/ECC | ef648e0 | 2026-10-01 | MIT (see LICENSE) | on-demand review checklists: `orkestra playbook security \| stack-web \| stack-mobile \| data \| ui-flow` |

Verbatim upstream files. `SKILL.md` → `SKILL.ref.md` and `agents/*.md` → `*.ref.md` so Claude Code never auto-loads
them. Nothing in this folder is read at runtime; the playbooks under `skills/run/references/` were distilled from it.
Refresh: `bash scripts/refresh-vendor-ecc.sh` (then update the commit/date above).

Deliberately NOT vendored (they would fight Orkestra's state machine, guards or token budget): ECC hooks, commands,
its planner/tdd/verification/orchestration skills and agents, continuous-learning, gateguard/delivery-gate, and the
language packs for stacks not used under ~/Projects.

`agents/database-reviewer.ref.md` includes patterns adapted from Supabase Agent Skills (MIT), as credited in that file.
