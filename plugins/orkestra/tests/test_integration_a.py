"""Small cross-agent loose ends found by the integrator of wave A."""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from orkestra_core import activity, monitor

_path = Path(__file__).resolve().parent.parent / "hooks" / "dispatch.py"
_spec = importlib.util.spec_from_file_location("dispatch_under_test", _path)
dispatch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatch)


class TestLooseEnds(unittest.TestCase):
    def test_a_status_row_has_a_mark_and_is_not_progress(self):
        self.assertNotEqual(monitor.KIND_MARK.get("status", " "), " ")
        self.assertIn("status", activity.ASIDE_KINDS)

    def test_git_context_runs_git_without_optional_locks(self):
        seen = []

        def fake_run(argv, **kw):
            seen.append(kw.get("env") or {})
            return mock.Mock(returncode=0, stdout="main\n")
        ev = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}, "cwd": "/tmp"}
        with mock.patch("subprocess.run", fake_run):
            dispatch.git_context(ev)
        self.assertTrue(seen)
        self.assertTrue(all(e.get("GIT_OPTIONAL_LOCKS") == "0" for e in seen))


if __name__ == "__main__":
    unittest.main()
