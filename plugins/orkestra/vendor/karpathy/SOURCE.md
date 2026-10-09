# vendor/karpathy — pinned, read-only references

| upstream | commit | date | license | used for |
|---|---|---|---|---|
| https://github.com/multica-ai/andrej-karpathy-skills | 2c60614 | 2026-04-20 | MIT (declared in upstream `.claude-plugin/plugin.json` and the SKILL frontmatter; the repo ships no LICENSE file) | surgical-diff / simplicity / surfaced-assumption rules for implementer, reviewer, architect |

`SKILL.md` is stored as `SKILL.ref.md` so Claude Code never auto-loads it. Nothing in this folder is read at runtime.
Refresh: `bash scripts/refresh-vendor-ecc.sh` (then update the commit/date above).
