"""FR-001 / FR-002 / SC-001 — doctor soft-pass when any engine is ready."""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

import helpers
from orkestra_core import cursorkit as CK


def _path_without_claude() -> str:
    """PATH with git/python but no `claude` binary (isolates the soft-pass cases)."""
    parts = []
    for d in ("/usr/bin", "/bin", str(Path(sys.executable).parent)):
        if Path(d).is_dir() and d not in parts:
            parts.append(d)
    for d in (os.environ.get("PATH") or "").split(os.pathsep):
        if d and d not in parts and not (Path(d) / "claude").exists():
            parts.append(d)
    return os.pathsep.join(parts)


class TestDoctorSoftPass(helpers.Sandbox):
    def setUp(self):
        super().setUp()
        self.chome = Path(os.environ["ORKESTRA_CURSOR_HOME"])
        self.safe_path = _path_without_claude()
        self.assertIsNone(shutil.which("claude", path=self.safe_path),
                          "fixture PATH must not resolve claude")
        self.doctor_env = {"PATH": self.safe_path, "COPILOT_HOME": str(self.tmp / "nocopilot")}

    def _install_cursor_hooks(self) -> None:
        self.chome.mkdir(parents=True, exist_ok=True)
        (self.chome / "hooks.json").write_text(json.dumps(CK.merge_hooks(None)))
        self.assertTrue(CK.hooks_installed())

    def test_cursor_hooks_only_passes_and_claude_is_soft(self):
        """E1 / FR-001 / FR-002 / SC-001: Cursor hooks ready, no claude → exit 0, soft claude."""
        self._install_cursor_hooks()
        cp = self.cli("doctor", check=False, env=self.doctor_env)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertNotRegex(cp.stdout, r"\[!!\]\s+claude\b")
        self.assertRegex(cp.stdout, r"\[--\]\s+claude\b")

    def test_no_engines_ready_fails(self):
        """E2 / FR-001 / SC-001: required OK but no claude/cursor/copilot → exit 1."""
        self.assertFalse(CK.hooks_installed())
        self.assertFalse((self.tmp / "nocopilot").exists())
        cp = self.cli("doctor", check=False, env=self.doctor_env)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)


if __name__ == "__main__":
    unittest.main()
