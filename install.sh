#!/usr/bin/env bash
# Orkestra installer (macOS / Linux). Idempotent — safe to run again for updates.
#
#   bash ./install.sh            # interactive
#   bash ./install.sh --yes      # accept defaults (Context7 + Playwright MCP, bridge token)
#   bash ./install.sh --no-mcp   # plugin only
#   bash ./install.sh --refresh-vendor   # re-pull pinned reference files from GitHub
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$ROOT/plugins/orkestra"
YES=0; NO_MCP=0; REFRESH=0; SKIP_TESTS=0
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    --no-mcp) NO_MCP=1 ;;
    --refresh-vendor) REFRESH=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m ✗ %s\033[0m\n' "$*" >&2; exit 1; }
ask()  { # ask "question" default(y|n)
  local q="$1" d="${2:-n}" r
  if [ "$YES" = 1 ]; then [ "$d" = y ]; return; fi
  read -r -p "$q [$( [ "$d" = y ] && echo Y/n || echo y/N )] " r || true
  r="${r:-$d}"; [[ "$r" =~ ^[YyEe] ]]
}

say "Ön koşullar"
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 bulunamadı (brew install python)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "python3 >= 3.9 gerekli ($("$PY" -V)). 'brew install python' ve PATH'te önce gelmesini sağla."
ok "python3 $("$PY" -c 'import platform; print(platform.python_version())') ($PY)"
command -v git >/dev/null || die "git bulunamadı"
ok "git"

HAS_CLAUDE=0
if command -v claude >/dev/null; then
  HAS_CLAUDE=1
  ok "claude $(claude --version 2>/dev/null | head -1)"
else
  warn "Claude Code CLI (claude) yok — Cursor-only kurulum yolu kullanılacak (https://code.claude.com/docs)"
fi
HAS_CURSOR=0
if command -v agent >/dev/null; then
  HAS_CURSOR=1
  ok "Cursor CLI agent $(agent --version 2>/dev/null | head -1 || echo present)"
fi
if [ -d "${HOME}/.cursor" ]; then
  HAS_CURSOR=1
fi
HAS_COPILOT=0
COPILOT_DIR="${COPILOT_HOME:-$HOME/.copilot}"
if [ -d "$COPILOT_DIR" ]; then
  HAS_COPILOT=1
  ok "Copilot home ($COPILOT_DIR)"
fi
chmod +x "$PLUGIN/bin/orkestra" "$ROOT/bridge/orkestra_bridge.py" 2>/dev/null || true
find "$PLUGIN" -type f -name "*.py" -path "*/hooks/*" -exec chmod +x {} + 2>/dev/null || true

if [ "$REFRESH" = 1 ]; then
  say "Vendor referansları yenileniyor"
  bash "$ROOT/scripts/refresh-vendor.sh"
fi

if [ "$SKIP_TESTS" = 0 ]; then
  say "Eklenti ve bridge Python testleri bu makinede koşuyor (birkaç dakika; sistem yüküne göre değişir)"
  ( cd "$PLUGIN/tests" && "$PY" -m unittest discover -s . -q ) || die "plugin testleri geçmedi — kurulum durduruldu"
  ( cd "$ROOT/bridge/tests" && "$PY" -m unittest discover -s . -q ) || die "bridge testleri geçmedi — kurulum durduruldu"
  ok "tüm testler yeşil"
fi

say "Claude Code plugin"
if [ "$HAS_CLAUDE" = 1 ]; then
  if claude plugin marketplace list 2>/dev/null | grep -q "orkestra-local"; then
    claude plugin marketplace update orkestra-local >/dev/null && ok "marketplace güncellendi"
  else
    claude plugin marketplace add "$ROOT" && ok "marketplace eklendi (orkestra-local)"
  fi
  claude plugin install orkestra@orkestra-local -s user && ok "plugin kurulu (yerinde yüklenir: $PLUGIN)"
  claude plugin validate "$ROOT" >/dev/null && ok "manifest doğrulandı"
else
  warn "Claude marketplace/plugin adımları atlandı (claude yok)"
fi

say "Global ayarlar ve öz-test"
"$PY" "$PLUGIN/bin/orkestra" doctor || warn "doctor bazı kontrollerde uyardı (yukarıya bak)"
ok "ayarlar: ~/.orkestra/config.json (guard'lar burada açılıp kapatılır)"

say "Cursor (skill, alt-ajan, kurulum)"
CURSOR_DEFAULT=n
[ "$HAS_CURSOR" = 1 ] && CURSOR_DEFAULT=y
if ask "Cursor skill, alt-ajan ve kurulum yapılsın mı? (~/.cursor; mevcut ayarlar korunur)" "$CURSOR_DEFAULT"; then
  "$PY" "$PLUGIN/bin/orkestra" cursor install || warn "cursor install başarısız"
  "$PY" "$PLUGIN/bin/orkestra" cursor status || true
fi

say "Copilot (hooks, kurulum)"
COPILOT_DEFAULT=n
[ "$HAS_COPILOT" = 1 ] && COPILOT_DEFAULT=y
if ask "Copilot hooks kurulsun mu? (~/.copilot; mevcut ayarlar korunur)" "$COPILOT_DEFAULT"; then
  "$PY" "$PLUGIN/bin/orkestra" copilot install || warn "copilot install başarısız"
  "$PY" "$PLUGIN/bin/orkestra" copilot status || true
fi

# FR-002 / E4: always ensure PATH shim (even if Cursor/Copilot ask was declined)
say "PATH shim (orkestra)"
if "$PY" -c "import sys; sys.path.insert(0, '$PLUGIN/lib'); from orkestra_core.cursorkit import ensure_path_shim; [print(a) for a in ensure_path_shim()]"; then
  ok "PATH shim (ensure_path_shim; ORKESTRA_SHIM_DIR or ~/.local/bin)"
else
  warn "PATH shim başarısız (ensure_path_shim / ORKESTRA_SHIM_DIR)"
fi

say "Kurulum sonrası sağlık"
"$PY" "$PLUGIN/bin/orkestra" doctor || warn "doctor bazı kontrollerde uyardı (yukarıya bak)"

if [ "$NO_MCP" = 0 ] && [ "$HAS_CLAUDE" = 1 ]; then
  say "MCP araçları (isteğe bağlı)"
  MCPS="$(claude mcp list 2>/dev/null || true)"
  if echo "$MCPS" | grep -qi "context7"; then ok "context7 zaten var"
  elif ask "Context7 (güncel kütüphane dokümanı) eklensin mi?" y; then
    claude mcp add --transport http context7 https://mcp.context7.com/mcp -s user && ok "context7 eklendi"
  fi
  if echo "$MCPS" | grep -qi "playwright"; then ok "playwright zaten var"
  elif command -v npx >/dev/null && ask "Playwright MCP eklensin mi?" y; then
    claude mcp add playwright -s user -- npx @playwright/mcp@latest && ok "playwright eklendi"
  fi
  if echo "$MCPS" | grep -qi "serena"; then ok "serena zaten var"
  elif command -v uv >/dev/null && ask "Serena eklensin mi?" n; then
    command -v serena >/dev/null || uv tool install -p 3.13 serena-agent || warn "serena-agent kurulamadı"
    if command -v serena >/dev/null; then
      claude mcp add --scope user serena -- serena start-mcp-server --context claude-code --project-from-cwd && ok "serena eklendi"
    fi
  fi
elif [ "$NO_MCP" = 0 ] && [ "$HAS_CLAUDE" = 0 ]; then
  warn "MCP adımları atlandı (Claude Code CLI gerekli)"
fi

say "Bridge (n8n / Telegram tetikleyici)"
if ask "Bridge token'ı oluşturulsun mu? (n8n ve Telegram'dan iş başlatmak için)" y; then
  "$PY" "$ROOT/bridge/orkestra_bridge.py" init
  if [ "$YES" = 0 ] && ask "Telegram botu şimdi bağlansın mı? (BotFather token'ı gerekir)" n; then
    "$PY" "$ROOT/bridge/orkestra_bridge.py" telegram-setup || warn "Telegram kurulumu tamamlanmadı"
  fi
  if [ "$(uname)" = "Darwin" ] && ask "Bridge açılışta otomatik başlasın mı? (LaunchAgent)" n; then
    "$PY" "$ROOT/bridge/orkestra_bridge.py" launchd
    launchctl unload "$HOME/Library/LaunchAgents/com.orkestra.bridge.plist" 2>/dev/null || true
    launchctl load -w "$HOME/Library/LaunchAgents/com.orkestra.bridge.plist" && ok "bridge LaunchAgent yüklendi"
  fi
  if { [ -n "${N8N_API_KEY:-}" ] || [ -n "${N8N_STATE:-}" ]; } \
    && ask "n8n workflow'ları N8N_* ortam değişkenleriyle aktarılsın mı?" n; then
    "$PY" "$ROOT/bridge/n8n_import.py" \
      || warn "n8n içe aktarma başarısız (N8N_URL, N8N_API_KEY veya N8N_STATE)"
  fi
fi

CURSOR_READY=0
if [ -f "${HOME}/.cursor/hooks.json" ] && grep -qi orkestra "${HOME}/.cursor/hooks.json" 2>/dev/null; then
  CURSOR_READY=1
fi
COPILOT_READY=0
if "$PY" "$PLUGIN/bin/orkestra" copilot status 2>/dev/null | grep -q '\[OK\] copilot hooks'; then
  COPILOT_READY=1
fi
ENGINE_READY=1
if [ "$HAS_CLAUDE" = 0 ] && [ "$CURSOR_READY" = 0 ] && [ "$COPILOT_READY" = 0 ]; then
  ENGINE_READY=0
  warn "Ne Claude Code (claude) ne Cursor Orkestra ne Copilot Orkestra kurulumu hazır görünüyor."
  warn "Claude Code kurun, veya: python3 $PLUGIN/bin/orkestra setup | cursor install | copilot install"
fi

echo
if [ "$ENGINE_READY" = 1 ]; then
  ok "Orkestra hazır."
else
  warn "Orkestra hazır değil — en az bir motor (Claude, Cursor veya Copilot) kurulu olmalı."
fi
cat <<EOF

  Kullanım (Claude Code, Cursor veya Copilot; workspace klasörünüzde, varsayılan ~/Projects):
    /orkestra:run <istek>
    /orkestra:status                 /orkestra:resume
    /orkestra:rollback
    /orkestra:ship                   (yalnız sen çağırırsın)

  Setup:     python3 $PLUGIN/bin/orkestra setup
  Cursor:    python3 $PLUGIN/bin/orkestra cursor install | status
  Copilot:   python3 $PLUGIN/bin/orkestra copilot install | status
  Ayarlar:   ~/.orkestra/config.json     Bridge: ~/.orkestra/bridge.secret.json
  Sağlık:    python3 $PLUGIN/bin/orkestra doctor
  n8n import: N8N_URL N8N_API_KEY|N8N_STATE python3 $ROOT/bridge/n8n_import.py
EOF

