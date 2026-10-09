#!/usr/bin/env bash
# Re-pull the pinned reference files (see plugins/orkestra/vendor/PINNED.md) from upstream HEAD.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
V="$ROOT/plugins/orkestra/vendor"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
clone() { git clone -q --depth 1 "https://github.com/$1" "$TMP/$2"; }
clone obra/superpowers sp; clone github/spec-kit sk; clone humanlayer/humanlayer hl; clone open-gsd/gsd-core gsd
clone bmad-code-org/BMAD-METHOD bmad; clone Fission-AI/OpenSpec os; clone EveryInc/compound-engineering-plugin ce
for s in systematic-debugging test-driven-development verification-before-completion subagent-driven-development writing-plans brainstorming receiving-code-review; do
  rm -rf "$V/superpowers/skills/$s"; cp -r "$TMP/sp/skills/$s" "$V/superpowers/skills/$s"
  [ -f "$V/superpowers/skills/$s/SKILL.md" ] && mv "$V/superpowers/skills/$s/SKILL.md" "$V/superpowers/skills/$s/SKILL.ref.md"
done
cp "$TMP/sk/templates/"{spec-template.md,plan-template.md,tasks-template.md,checklist-template.md} "$V/spec-kit/templates/"
cp "$TMP/sk/templates/commands/"{clarify.md,analyze.md} "$V/spec-kit/templates/"
cp "$TMP/hl/LICENSE" "$V/humanlayer/LICENSE"; [ -f "$TMP/hl/NOTICE" ] && cp "$TMP/hl/NOTICE" "$V/humanlayer/NOTICE"   # Apache-2.0: notice travels with the files (full text: LICENSE-APACHE-2.0.txt)
for a in codebase-analyzer codebase-locator codebase-pattern-finder; do cp "$TMP/hl/.claude/agents/$a.md" "$V/humanlayer/agents/$a.ref.md"; done
cp "$TMP/gsd/gsd-core/references/"{execute-mvp-tdd.md,verification-patterns.md,context-budget.md} "$V/gsd-core/references/"
cp "$TMP/bmad/skills/bmad-build/plan-template.md" "$V/bmad/bmad-build/"; rm -rf "$V/bmad/bmad-build/review-prompts"; cp -r "$TMP/bmad/skills/bmad-build/review-prompts" "$V/bmad/bmad-build/"
cp "$TMP/os/docs/"{concepts.md,existing-projects.md} "$V/openspec/docs/"
cp "$TMP/ce/skills/ce-compound/SKILL.md" "$V/compound/ce-compound-SKILL.ref.md"
bash "$ROOT/scripts/refresh-vendor-ecc.sh"   # ECC + karpathy (own SOURCE.md files)
echo "vendor refreshed from upstream HEAD — update the commit column in $V/PINNED.md:"
for d in sp sk hl gsd bmad os ce; do echo "  $d $(git -C "$TMP/$d" log -1 --format='%h %cs')"; done
