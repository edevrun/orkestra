"""Anti-hallucination / anti-gaming rules of the evidence layer."""
import json
import re
import unittest

import helpers
from test_flow import FlowBase, PLAN
from orkestra_core import guards
from orkestra_core.paths import DEFAULT_GLOBAL_CONFIG


class TestPlanFiles(FlowBase):
    def test_nonexistent_files_must_be_marked_new(self):
        self.start()
        self.advance_to_approve_with(PLAN.replace("`calc.py` (değişir)", "`src/ghost.py` (değişir)"), expect_ok=False,
                                     needle="src/ghost.py")
        ok_plan = PLAN.replace("`calc.py` (değişir)", "`calc.py` (değişir), `src/new_mod.py` (yeni)")
        (self.fdir / "plan.md").write_text(ok_plan)
        cp = self.cli("plan", "check", "-p", "demo", check=False)
        self.assertEqual(cp.returncode, 0, cp.stdout)

    def advance_to_approve_with(self, plan, expect_ok, needle=""):
        self.phase_next()
        (self.fdir / "context.md").write_text("# Context\n" + ("calc.py defines add(); tests in tests/. " * 10))
        self.cli("gate", "baseline", "-p", "demo")
        self.phase_next()
        from test_flow import SPEC
        (self.fdir / "spec.md").write_text(SPEC)
        self.phase_next()
        (self.fdir / "plan.md").write_text(plan)
        (self.fdir / "reviews" / "plan-critic.md").write_text("VERDICT: PASS\n")
        cp = self.phase_next(expect_ok=expect_ok)
        if needle:
            self.assertIn(needle, cp.stderr)


class TestTddRules(FlowBase):
    def setUp(self):
        super().setUp()
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        self.cli("task", "start", "T001", "-p", "demo")
        self.test = self.proj / "tests" / "test_sub.py"
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Write", "cwd": str(self.proj),
                                 "tool_input": {"file_path": str(self.test)}})
        self.test.write_text("import unittest, sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
                             "from calc import sub\n\nclass T(unittest.TestCase):\n    def test_sub(self):\n"
                             "        self.assertEqual(sub(5, 3), 2)\n")
        self.cmd = "python3 -m unittest tests.test_sub -q"
        self.cli("tdd", "red", "T001", "-p", "demo", "--", self.cmd)
        with open(self.proj / "calc.py", "a") as fh:
            fh.write("\n\ndef sub(a, b):\n    return a - b\n")

    def test_green_must_use_same_command(self):
        cp = self.cli("tdd", "green", "T001", "-p", "demo", "--", "python3 -c 'print(1)'", check=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("same command as red", cp.stderr)
        self.cli("tdd", "green", "T001", "-p", "demo", "--why", "runner moved to make", "--", "python3 -m unittest discover -s tests -q")
        st = json.loads(self.cli("status", "--json", "-p", "demo").stdout)
        self.assertIn("runner moved to make", " ".join(st["tasks"]["T001"]["green"]["deviations"]))

    def test_test_weakened_after_red_is_refused(self):
        self.test.write_text(self.test.read_text().replace("self.assertEqual(sub(5, 3), 2)", "self.assertTrue(True)"))
        cp = self.cli("tdd", "green", "T001", "-p", "demo", "--", self.cmd, check=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("changed after the red run", cp.stderr)

    def test_honest_cycle_passes_and_report_shows_it(self):
        self.cli("tdd", "green", "T001", "-p", "demo", "--", self.cmd)
        self.cli("task", "done", "T001", "-p", "demo")
        block = self.cli("report", "evidence", "-p", "demo").stdout
        self.assertIn("| T001 done | 1 | 0 |", block)
        self.assertIn("Test bütünlüğü: temiz", block)


class TestTaskNeedsTestChange(FlowBase):
    def test_no_test_file_no_done(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        self.cli("task", "start", "T001", "-p", "demo")
        self.cli("tdd", "red", "T001", "-p", "demo", "--", "python3", "-c", "import sys; sys.exit(1)")
        self.cli("tdd", "green", "T001", "-p", "demo", "--why", "fixture", "--", "python3", "-c", "print(1)")
        cp = self.cli("task", "done", "T001", "-p", "demo", check=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("no test file was added or changed", cp.stderr)


class TestIntegrity(FlowBase):
    def setUp(self):
        super().setUp()
        self.start()

    def write_plan_exceptions(self, paths):
        appr = self.proj / ".orkestra" / "runs" / self.rid / "approved"
        appr.mkdir(parents=True, exist_ok=True)
        (appr / "plan.md").write_text("## İstisnalar\n" + "\n".join(f"- `{p}` — kullanıcı onayladı" for p in paths) + "\n")

    def integrity(self):
        return self.cli("integrity", "check", "-p", "demo", check=False)

    def test_clean(self):
        self.assertEqual(self.integrity().returncode, 0)

    def test_removed_test_detected(self):
        t = self.proj / "tests" / "test_calc.py"
        t.write_text(re.sub(r"    def test_add.*", "    pass", t.read_text(), flags=re.S))
        cp = self.integrity()
        self.assertEqual(cp.returncode, 1)
        self.assertIn("test count dropped 1 → 0", cp.stdout)
        self.write_plan_exceptions(["tests/test_calc.py"])
        self.assertEqual(self.integrity().returncode, 0)

    def test_skip_marker_detected(self):
        t = self.proj / "tests" / "test_calc.py"
        t.write_text(t.read_text().replace("    def test_add", "    @unittest.skip('later')\n    def test_add"))
        cp = self.integrity()
        self.assertIn("skip/only/xfail", cp.stdout)

    def test_suppression_detected(self):
        (self.proj / "calc.py").write_text("def add(a, b):  # type: ignore\n    return a + b\n")
        cp = self.integrity()
        self.assertIn("suppression", cp.stdout)

    def test_js_only_and_ts_ignore(self):
        (self.proj / "src").mkdir()
        (self.proj / "src" / "a.test.ts").write_text("it('a', () => {})\nit('b', () => {})\n")
        self.git(self.proj, "add", "-A")
        self.git(self.proj, "commit", "-qm", "js")
        self.cli("abort", "-p", "demo", "restart")
        self.start("Second")
        (self.proj / "src" / "a.test.ts").write_text("it.only('a', () => {})\n// @ts-ignore\nit('b', () => {})\n")
        cp = self.integrity()
        self.assertIn("skip/only", cp.stdout)
        self.assertIn("suppression", cp.stdout)


class TestVerificationEvidence(FlowBase):
    def test_manual_claim_needs_recorded_log(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        from orkestra_core import evidence as E
        from pathlib import Path
        spec = (self.fdir / "spec.md").read_text()
        ver = "| FR-001 | manual CLI check looked fine | PASS |\n| SC-001 | gate unit | PASS |\n"
        probs = E.verification_problems(Path(self.proj), self.rid, spec, ver, ["unit"])
        self.assertTrue(any("manual check must cite" in p for p in probs), probs)
        out = self.cli("evidence", "FR-001", "-p", "demo", "--", "python3", "-c", "print('ok')").stdout
        rel = re.search(r"(evidence/\S+\.log)", out).group(1)
        ver2 = f"| FR-001 | manual CLI check `{rel}` | PASS |\n| SC-001 | gate unit | PASS |\n"
        self.assertEqual(E.verification_problems(Path(self.proj), self.rid, spec, ver2, ["unit"]), [])


class TestNestedCommandGuards(unittest.TestCase):
    def test_commands_after_double_dash_are_guarded(self):
        cfg = json.loads(json.dumps(DEFAULT_GLOBAL_CONFIG))
        for c in ["orkestra tdd red T001 -- git push origin main", "orkestra evidence x -- cat .env",
                  "orkestra tdd green T1 -p demo -- bash -c 'git commit -am x'"]:
            ev = {"tool_name": "Bash", "tool_input": {"command": c}, "cwd": "/w/proj"}
            self.assertTrue(guards.pre_tool_decision(ev, cfg, None, []).denied, c)
        ev = {"tool_name": "Bash", "tool_input": {"command": "orkestra tdd red T001 -- npm test"}, "cwd": "/w/proj"}
        self.assertFalse(guards.pre_tool_decision(ev, cfg, None, []).denied)


if __name__ == "__main__":
    unittest.main()
