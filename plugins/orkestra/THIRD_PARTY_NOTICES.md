# Third-party notices

Orkestra's own code and documents are released under the MIT License (see `LICENSE` in this folder).

The folder `vendor/` contains files copied from other open-source projects. They are
**read-only references**: nothing in that folder is loaded or executed at runtime; Orkestra's playbooks and
agent prompts were written with them as a source of ideas. Each folder keeps its upstream licence file, and
those files stay under their own licences, listed here. Pinned commits and dates: `vendor/PINNED.md`.

| Folder (`vendor/…`) | Upstream | Commit | Licence | Copyright notice | Licence files in the folder |
|---|---|---|---|---|---|
| `superpowers` | https://github.com/obra/superpowers | `8ca22db` | MIT | Copyright (c) 2025 Jesse Vincent | LICENSE |
| `spec-kit` | https://github.com/github/spec-kit | `ae5ade7` | MIT | Copyright GitHub, Inc. | LICENSE |
| `bmad` | https://github.com/bmad-code-org/BMAD-METHOD | `3cae711` | MIT (name and logo are trademarks, see TRADEMARK.md) | Copyright (c) 2025 BMad Code, LLC | LICENSE, TRADEMARK.md |
| `gsd-core` | https://github.com/open-gsd/gsd-core | `c1df571` | MIT | Copyright (c) 2026 Open GSD | LICENSE |
| `openspec` | https://github.com/Fission-AI/OpenSpec | `2500d6d` | MIT | Copyright (c) 2024 OpenSpec Contributors | LICENSE |
| `humanlayer` | https://github.com/humanlayer/humanlayer | `99abe67` | Apache-2.0 | Copyright (c) 2024, humanlayer Authors | LICENSE (upstream notice), LICENSE-APACHE-2.0.txt (full text) |
| `compound` | https://github.com/EveryInc/compound-engineering-plugin | `9af474a` | MIT | Copyright (c) 2025 Every | LICENSE |
| `ecc` | https://github.com/affaan-m/ECC | `ef648e0` | MIT | Copyright (c) 2026 Affaan Mustafa | LICENSE |
| `karpathy` | https://github.com/multica-ai/andrej-karpathy-skills | `2c60614` | MIT (declared in upstream plugin.json; no upstream LICENSE file) | no holder named upstream (author field: forrestchang) | LICENSE (annotated), upstream-plugin.json |

Notes:

- **humanlayer (Apache-2.0).** Three agent definitions are redistributed, copied from upstream
  `.claude/agents/` and renamed from `*.md` to `*.ref.md` so that no tool auto-loads them. The upstream
  repository ships no `NOTICE` file at the pinned commit. The full licence text is in
  `vendor/humanlayer/LICENSE-APACHE-2.0.txt`.
- **ecc.** `agents/database-reviewer.ref.md` states that it includes patterns adapted from Supabase Agent
  Skills (MIT); that credit line is kept in the file.
- **bmad.** "BMad", "BMAD-METHOD" and related names and logos are trademarks of BMad Code, LLC
  (`vendor/bmad/TRADEMARK.md`). Orkestra is not affiliated with or endorsed by BMad Code.
- **karpathy.** Upstream has no licence file; see the note at the top of
  `vendor/karpathy/LICENSE`.
- Files in every vendor folder that upstream names `SKILL.md` or `<agent>.md` are stored as `*.ref.md`
  for the same reason as above. Orkestra is an independent project and is not affiliated with, sponsored by
  or endorsed by any of the projects listed here, nor by Anthropic (Claude Code) or Anysphere (Cursor).

To refresh the references from upstream: `bash install.sh --refresh-vendor` (from the repository root) (then update `PINNED.md` and
this file if a licence or copyright line changed).
