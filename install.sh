#!/usr/bin/env bash
# Orkestra installer (macOS / Linux). Idempotent — safe to run again for updates.
#
#   bash ~/Projects/orkestra/install.sh            # interactive
#   bash ~/Projects/orkestra/install.sh --yes      # accept defaults (Context7 + Playwright MCP, bridge token)
#   bash ~/Projects/orkestra/install.sh --no-mcp   # plugin only
#   bash ~/Projects/orkestra/install.sh --refresh-vendor   # re-pull pinned reference files from GitHub
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

# ------------------------------------------------------------------ 0. prerequisites
say "Ön koşullar"
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 bulunamadı (brew install python)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "python3 >= 3.9 gerekli ($("$PY" -V)). 'brew install python' ve PATH'te önce gelmesini sağla."
ok "python3 $("$PY" -c 'import platform; print(platform.python_version())') ($PY)"
command -v git >/dev/null || die "git bulunamadı"
ok "git"
command -v claude >/dev/null || die "Claude Code CLI (claude) bulunamadı — https://code.claude.com/docs"
ok "claude $(claude --version 2>/dev/null | head -1)"
chmod +x "$PLUGIN/bin/orkestra" "$PLUGIN/hooks/dispatch.py" "$ROOT/bridge/orkestra_bridge.py" 2>/dev/null || true

# ------------------------------------------------------------------ vendor refresh (optional)
if [ "$REFRESH" = 1 ]; then
  say "Vendor referansları yenileniyor"
  bash "$ROOT/scripts/refresh-vendor.sh"
fi

# ------------------------------------------------------------------ 1. tests on THIS machine
if [ "$SKIP_TESTS" = 0 ]; then
  say "Test paketi bu makinede koşuyor (boş makinede ~2 dk; yük altında daha uzun)"
  ( cd "$PLUGIN/tests" && "$PY" -m unittest discover -s . -q ) || die "plugin testleri geçmedi — kurulum durduruldu"
  ( cd "$ROOT/bridge/tests" && "$PY" -m unittest discover -s . -q ) || die "bridge testleri geçmedi — kurulum durduruldu"
  ok "tüm testler yeşil"
fi

# ------------------------------------------------------------------ 2. plugin
say "Claude Code plugin"
if claude plugin marketplace list 2>/dev/null | grep -q "orkestra-local"; then
  claude plugin marketplace update orkestra-local >/dev/null && ok "marketplace güncellendi"
else
  claude plugin marketplace add "$ROOT" && ok "marketplace eklendi (orkestra-local)"
fi
claude plugin install orkestra@orkestra-local -s user && ok "plugin kurulu (yerinde yüklenir: $PLUGIN)"
claude plugin validate "$ROOT" >/dev/null && ok "manifest doğrulandı"

# ------------------------------------------------------------------ 3. global config + self-test
say "Global ayarlar ve hook öz-testi"
"$PY" "$PLUGIN/bin/orkestra" doctor || warn "doctor bazı kontrollerde uyardı (yukarıya bak)"
ok "ayarlar: ~/.orkestra/config.json (guard'lar burada açılıp kapatılır)"

# ------------------------------------------------------------------ 4. MCP tools (optional)
if [ "$NO_MCP" = 0 ]; then
  say "MCP araçları (isteğe bağlı)"
  MCPS="$(claude mcp list 2>/dev/null || true)"
  if echo "$MCPS" | grep -qi "context7"; then ok "context7 zaten var"
  elif ask "Context7 (güncel kütüphane dokümanı, ajanların eski API uydurmasını engeller) eklensin mi?" y; then
    claude mcp add --transport http context7 https://mcp.context7.com/mcp -s user && ok "context7 eklendi"
  fi
  if echo "$MCPS" | grep -qi "playwright"; then ok "playwright zaten var"
  elif command -v npx >/dev/null && ask "Playwright MCP (web UI doğrulaması için gerçek tarayıcı) eklensin mi?" y; then
    claude mcp add playwright -s user -- npx @playwright/mcp@latest && ok "playwright eklendi"
  fi
  if echo "$MCPS" | grep -qi "serena"; then ok "serena zaten var"
  elif command -v uv >/dev/null && ask "Serena (LSP ile sembol düzeyi kod gezinme; büyük repolarda token tasarrufu) eklensin mi?" n; then
    command -v serena >/dev/null || uv tool install -p 3.13 serena-agent || warn "serena-agent kurulamadı"
    if command -v serena >/dev/null; then
      claude mcp add --scope user serena -- serena start-mcp-server --context claude-code --project-from-cwd && ok "serena eklendi"
    fi
  fi
fi

# ------------------------------------------------------------------ 5. bridge (n8n / Telegram)
say "Bridge (n8n / Telegram tetikleyici)"
if ask "Bridge token'ı oluşturulsun mu? (n8n ve Telegram'dan iş başlatmak için)" y; then
  "$PY" "$ROOT/bridge/orkestra_bridge.py" init
  if [ "$YES" = 0 ] && ask "Telegram botu şimdi bağlansın mı? (BotFather token'ı gerekir; OpenClaw'ın botunu KULLANMA)" n; then
    "$PY" "$ROOT/bridge/orkestra_bridge.py" telegram-setup || warn "Telegram kurulumu tamamlanmadı (sonra: orkestra_bridge.py telegram-setup)"
  fi
  if [ "$(uname)" = "Darwin" ] && ask "Bridge açılışta otomatik başlasın mı? (LaunchAgent)" n; then
    "$PY" "$ROOT/bridge/orkestra_bridge.py" launchd
    launchctl unload "$HOME/Library/LaunchAgents/com.orkestra.bridge.plist" 2>/dev/null || true
    launchctl load -w "$HOME/Library/LaunchAgents/com.orkestra.bridge.plist" && ok "bridge LaunchAgent yüklendi"
  fi
  if [ -f "$ROOT/../n8n/bootstrap-state.json" ] && ask "n8n workflow'ları (webhook ile iş başlat + bildirim) yerel n8n'e aktarılsın mı?" n; then
    "$PY" "$ROOT/bridge/n8n_import.py" || warn "n8n içe aktarma başarısız (n8n çalışıyor mu? cd ~/Projects/n8n && bash start.sh)"
  fi
fi

echo
ok "Orkestra hazır."
cat <<EOF

  Kullanım (Claude Code'da, herhangi bir projede ya da ~/Projects kökünde):
    /orkestra:run <istek>            uçtan uca: keşif → soru → plan → onay → TDD → doğrulama → review → teslim
    /orkestra:status                 durum      /orkestra:resume   kaldığı yerden devam
    /orkestra:rollback               bir run'ın değişikliklerini geri al (git'e dokunmaz)
    /orkestra:ship                   commit + push (yalnız sen çağırırsın)

  Ayarlar:   ~/.orkestra/config.json     Bridge: ~/.orkestra/bridge.secret.json
  Sağlık:    orkestra doctor  (Claude Code içinde) · python3 $PLUGIN/bin/orkestra doctor
EOF
