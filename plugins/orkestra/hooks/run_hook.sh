#!/bin/sh
# Starts one of Orkestra's Python hook scripts with whichever Python 3 this machine has.
#
#   sh run_hook.sh <path/to/script.py> <hook arguments...>
#
# hooks.json names this launcher instead of a bare `python3`: Claude Code runs hook command lines through a POSIX
# shell (Git Bash on Windows), and the name `python3` does not exist everywhere. python.org and winget installs on
# Windows provide `python` and the `py` launcher, and there `python3` may be the Microsoft Store stub, which is found
# by name and opens the Store instead of running Python.
#
# Order: python3, python, py -3.
#   macOS / Linux  python3 is exec'd at once (no probe, no extra process, same behaviour as before).
#   Windows        every candidate is probed first (runs, Python 3.9 or newer), so a stub is skipped.
# The exit code and the output of the hook pass through unchanged (exit 2 blocks a tool call). With no usable Python
# the launcher exits 127 and says why on stderr: the same effect as the old `python3: command not found`.
script=$1
if [ $# -eq 0 ] || [ -z "$script" ]; then
  echo "usage: run_hook.sh <script.py> [arguments]" >&2
  exit 64
fi
shift

probe='import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'
windows=
if [ "${OS:-}" = "Windows_NT" ] || [ -n "${MSYSTEM:-}" ]; then
  windows=1
fi

if [ -z "$windows" ] && command -v python3 >/dev/null 2>&1; then
  exec python3 "$script" "$@"
fi
for py in python3 python; do
  command -v "$py" >/dev/null 2>&1 || continue
  "$py" -c "$probe" >/dev/null 2>&1 || continue
  exec "$py" "$script" "$@"
done
if command -v py >/dev/null 2>&1 && py -3 -c "$probe" >/dev/null 2>&1; then
  exec py -3 "$script" "$@"
fi

# The message below is chosen without Python (there is none), but in the same order as i18n.lang(): ORKESTRA_LANG,
# then "lang" in $ORKESTRA_HOME/config.json, then LC_ALL, LC_MESSAGES, LANG, LANGUAGE, then English. Only shell builtins
# are used: on this path PATH may hold nothing else.
norm_lang() {                       # tr, TR, tr_TR.UTF-8, tr-TR, Turkish, Turkce, Türkçe -> tr ; en, en_US.UTF-8, English -> en
  case $1 in
    [Tt][Rr]|[Tt][Rr][._@:-]*|[Tt][Uu][Rr][Kk][Ii][Ss][Hh]|[Tt][Uu][Rr][Kk][Cc][Ee]|türkçe|Türkçe|TÜRKÇE) echo tr ;;
    [Ee][Nn]|[Ee][Nn][._@:-]*|[Ee][Nn][Gg][Ll][Ii][Ss][Hh]|[Ii][Nn][Gg][Ii][Ll][Ii][Zz][Cc][Ee]) echo en ;;
  esac
}
hook_lang() {
  hl=$(norm_lang "${ORKESTRA_LANG:-}")
  if [ -n "$hl" ]; then echo "$hl"; return; fi
  case ${ORKESTRA_LANG:-} in ""|[Aa][Uu][Tt][Oo]) ;; *) echo en; return ;; esac     # explicit but unsupported: English
  hh=${ORKESTRA_HOME:-$HOME/.orkestra}
  case $hh in "~"|"~/"*) hh=$HOME${hh#"~"} ;; esac
  if [ -r "$hh/config.json" ]; then
    while IFS= read -r hline || [ -n "$hline" ]; do
      case $hline in
        *\"lang\"*)
          hrest=${hline#*\"lang\"}; hrest=${hrest#*:}; hrest=${hrest#*\"}; hl=$(norm_lang "${hrest%%\"*}")
          if [ -n "$hl" ]; then echo "$hl"; return; fi
          break ;;
      esac
    done < "$hh/config.json"
  fi
  set -f
  for hvar in LC_ALL LC_MESSAGES LANG LANGUAGE; do
    eval "hval=\${$hvar:-}"
    oldifs=$IFS; IFS=:
    for hpart in $hval; do
      hl=$(norm_lang "$hpart")
      if [ -n "$hl" ]; then IFS=$oldifs; set +f; echo "$hl"; return; fi
    done
    IFS=$oldifs
  done
  set +f
  echo en
}

case $(hook_lang) in
  tr)
    echo "orkestra: Python 3.9 veya üstü bulunamadı (python3, python ve py -3 denendi). https://www.python.org/downloads/ adresinden kurup terminali yeniden aç." >&2 ;;
  *)
    echo "orkestra: no Python 3.9+ found (tried python3, python and py -3). Install it from https://www.python.org/downloads/ and reopen the terminal." >&2 ;;
esac
exit 127
