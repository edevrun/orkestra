#!/usr/bin/env python3
"""Single entry point for all Orkestra hooks:  python3 dispatch.py <EventName>  (event JSON on stdin).

A stub on purpose. The hook code is lib/orkestra_core/hookmain.py: an entry script is never cached as bytecode, so
keeping 1200 lines here meant compiling them on every hook call. orkestra_core.hookboot ends events that provably do
nothing right away, and otherwise executes the cached hookmain code in THIS namespace: `import dispatch` still
provides every name (handlers, guards, emit, log, ...), and patching one here changes what the handlers see.
Edit hookmain.py, not this file.
"""
import os
import sys

_LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "lib")
sys.path.insert(0, _LIB)
# A plugin directory this user cannot write (read-only install, container) cannot keep its bytecode, so every hook would
# recompile every module (about 4x the CPU). Keep the bytecode under ORKESTRA_HOME then. PYTHONPYCACHEPREFIX (or -X
# pycache_prefix) set by the user wins; PYTHONDONTWRITEBYTECODE=1 means no bytecode is written anywhere.
if sys.pycache_prefix is None and not sys.dont_write_bytecode:
    _pkg = os.path.join(_LIB, "orkestra_core")
    _pyc = os.path.join(_pkg, "__pycache__")
    if not os.access(_pyc if os.path.isdir(_pyc) else _pkg, os.W_OK):
        sys.pycache_prefix = os.path.join(os.environ.get("ORKESTRA_HOME") or os.path.expanduser("~/.orkestra"), "pycache")

from orkestra_core import hookboot  # noqa: E402

hookboot.start(globals())
