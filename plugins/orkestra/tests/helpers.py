"""Shared test helpers (stdlib unittest only — the suite must run on a bare macOS python3)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
LIB = PLUGIN / "lib"
BIN = PLUGIN / "bin" / "orkestra"
DISPATCH = PLUGIN / "hooks" / "dispatch.py"
CURSOR_HOOK = PLUGIN / "hooks" / "cursor_hook.py"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


REF_NOMINAL = 0.03      # CPU seconds the reference loop takes on an idle, current machine
REF_CAP = 6.0           # a real regression here is 10x and more: the allowance never grows enough to hide one


def _reference_cpu() -> float:
    t, x = time.process_time(), 0
    for i in range(300_000):
        x += i * i % 7
    return time.process_time() - t


def assert_cpu_under(test, seconds: float, fn, msg: str = ""):
    """Run fn() and require its CPU time under a budget — "this must stay linear" checks.

    Wall-clock limits failed on the machine they were written for: 2026-10-05, load average 100+, one of them
    measured 1.82 s against 1.5 s and stopped install.sh. CPU time ignores waiting for a core; what a busy machine
    still slows down (shared caches, throttling) is measured with a fixed reference loop and widens the budget, at
    most REF_CAP times."""
    before = _reference_cpu()
    t = time.process_time()
    result = fn()
    used = time.process_time() - t
    factor = min(REF_CAP, max(1.0, max(before, _reference_cpu()) / REF_NOMINAL))
    test.assertLess(used, seconds * factor, f"{msg} (cpu {used:.2f}s, budget {seconds}s x {factor:.1f})")
    return result


class Sandbox(unittest.TestCase):
    """Isolated ORKESTRA_HOME + workspace + a git project with a tiny passing test gate."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="orkestra-test-")).resolve()
        self.home = self.tmp / "home"
        self.ws = self.tmp / "Projects"
        self.ws.mkdir()
        # the suite may be started from inside an agent's shell (Claude Code / Cursor): the CLI must not see that
        agent_env = ("CLAUDECODE", "CURSOR_AGENT", "CURSOR_CONVERSATION_ID", "ORKESTRA_CURSOR_HOME", "ORKESTRA_SHIM_DIR",
                     "ORKESTRA_CURSOR_BIN", "ORKESTRA_CLAUDE_BIN", "CLAUDE_CONFIG_DIR")
        self._env_backup = {k: os.environ.get(k) for k in ("ORKESTRA_HOME", "ORKESTRA_WORKSPACE", "ORKESTRA_PROJECT",
                                                           "ORKESTRA_HEADLESS") + agent_env}
        os.environ["ORKESTRA_HOME"] = str(self.home)
        os.environ["ORKESTRA_WORKSPACE"] = str(self.ws)
        os.environ.pop("ORKESTRA_PROJECT", None)
        os.environ.pop("ORKESTRA_HEADLESS", None)
        for k in agent_env:
            os.environ.pop(k, None)
        os.environ["ORKESTRA_CURSOR_HOME"] = str(self.tmp / "cursor-home")      # never the user's ~/.cursor
        os.environ["ORKESTRA_SHIM_DIR"] = str(self.tmp / "shim-bin")

    def tearDown(self) -> None:
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ fixtures
    def make_project(self, name: str = "demo", git: bool = True) -> Path:
        p = self.ws / name
        p.mkdir()
        (p / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (p / "tests").mkdir()
        (p / "tests" / "test_calc.py").write_text(
            "import unittest, sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
            "from calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        (p / "Makefile").write_text("test:\n\tpython3 -m unittest discover -s tests -q\n")
        if git:
            self.git(p, "init", "-q")
            self.git(p, "config", "user.email", "t@example.com")
            self.git(p, "config", "user.name", "t")
            self.git(p, "add", "-A")
            self.git(p, "commit", "-q", "-m", "init")
        return p

    @staticmethod
    def git(p: Path, *args: str) -> str:
        cp = subprocess.run(["git", "-C", str(p), *args], capture_output=True, text=True)
        if cp.returncode != 0:
            raise AssertionError(f"git {args} failed: {cp.stderr}")
        return cp.stdout

    def cli(self, *args: str, cwd: Path = None, check: bool = True, env: dict = None) -> subprocess.CompletedProcess:
        e = dict(os.environ)
        if env:
            e.update(env)
        cp = subprocess.run([sys.executable, str(BIN), *args], cwd=str(cwd or self.tmp), capture_output=True,
                            text=True, env=e)
        if check and cp.returncode != 0:
            raise AssertionError(f"orkestra {' '.join(args)} → {cp.returncode}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
        return cp

    def hook(self, event: str, payload: dict) -> dict:
        payload = dict(payload)
        payload.setdefault("hook_event_name", event)
        cp = subprocess.run([sys.executable, str(DISPATCH), event], input=json.dumps(payload), capture_output=True,
                            text=True, env=dict(os.environ))
        if cp.returncode != 0:
            raise AssertionError(f"hook {event} exit {cp.returncode}: {cp.stderr}")
        return json.loads(cp.stdout) if cp.stdout.strip() else {}

    @staticmethod
    def denied(out: dict) -> bool:
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
