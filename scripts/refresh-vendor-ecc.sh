#!/usr/bin/env bash
# Re-pull the pinned ECC + karpathy reference files (plugins/orkestra/vendor/{ecc,karpathy}/SOURCE.md) from upstream HEAD.
# Standalone on purpose: it touches only vendor/ecc and vendor/karpathy. scripts/refresh-vendor.sh may call it
# (sources: plugins/orkestra/vendor/ecc/SOURCE.md, vendor/karpathy/SOURCE.md). References only — nothing here is auto-loaded by Claude Code.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
V="${ORKESTRA_VENDOR_DIR:-$ROOT/plugins/orkestra/vendor}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
clone() { git clone -q --depth 1 "https://github.com/$1" "$TMP/$2"; }
clone affaan-m/ECC ecc; clone multica-ai/andrej-karpathy-skills kp
E="$V/ecc"; K="$V/karpathy"
mkdir -p "$E/agents" "$E/skills" "$E/rules" "$K"
cp "$TMP/ecc/LICENSE" "$E/LICENSE"
for a in security-reviewer silent-failure-hunter typescript-reviewer react-reviewer database-reviewer pr-test-analyzer; do
  cp "$TMP/ecc/agents/$a.md" "$E/agents/$a.ref.md"
done
for s in security-review click-path-audit database-migrations prisma-patterns e2e-testing strategic-compact search-first; do
  rm -rf "$E/skills/$s"; cp -r "$TMP/ecc/skills/$s" "$E/skills/$s"
  [ -f "$E/skills/$s/SKILL.md" ] && mv "$E/skills/$s/SKILL.md" "$E/skills/$s/SKILL.ref.md"
done
for r in common/security.md typescript/security.md react/security.md react/hooks.md web/security.md \
         react-native/security.md react-native/performance.md react-native/production-readiness.md react-native/patterns.md; do
  mkdir -p "$E/rules/$(dirname "$r")"; cp "$TMP/ecc/rules/$r" "$E/rules/$r"
done
cp "$TMP/kp/skills/karpathy-guidelines/SKILL.md" "$K/SKILL.ref.md"
cp "$TMP/kp/EXAMPLES.md" "$K/EXAMPLES.md"
echo "ecc + karpathy refreshed from upstream HEAD — update the commit lines in $E/SOURCE.md and $K/SOURCE.md:"
for d in ecc kp; do echo "  $d $(git -C "$TMP/$d" log -1 --format='%h %cs')"; done
