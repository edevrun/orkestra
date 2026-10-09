"""End-to-end CLI + hook flow on real temporary git/non-git projects (no network, no model)."""
import json
import re
import unittest
from pathlib import Path

import helpers

UNITTEST = "python3 -m unittest discover -s tests -q"

SPEC = """---
feature: x
status: draft
---
# Subtract

## Amaç ve değer
Kullanıcı iki sayıyı çıkarabilsin.

## Gereksinimler
- **FR-001**: `sub(a, b)` a-b döndürür MUST.

## Başarı kriterleri
- **SC-001**: `sub(5, 3) == 2` testi geçer.

## Sınırlar
- Her zaman: mevcut add davranışı korunur.
- Asla: bağımlılık ekleme.

## Netleştirmeler
### Oturum 2026-10-04
- Soru gerekmedi — istek tek anlamlı.
"""

PLAN = """---
feature: x
risk: low
approved_dependencies: []
---
# Plan — Subtract

## Genel kısıtlar
- add() değişmez.

## Görevler

### T001 — sub fonksiyonu
- Dosyalar: `calc.py` (değişir), `tests/test_sub.py` (yeni)
- Bağımlı: —
- Kabul: Given 5 ve 3 When sub Then 2 (FR-001)
- Test: `python3 -m unittest tests.test_sub -q`
- TDD: required

## Test stratejisi ve izlenebilirlik
| FR-001 | test_sub | unit |
| SC-001 | test_sub | unit |
"""


class FlowBase(helpers.Sandbox):
    def setUp(self):
        super().setUp()
        self.proj = self.make_project("demo")
        self.cli("init", "-p", "demo")
        cfgp = self.proj / ".orkestra" / "config.json"
        cfg = json.loads(cfgp.read_text())
        cfg["gates"] = [{"name": "unit", "cmd": UNITTEST, "kind": "test", "timeout": 120, "cwd": "."}]
        cfgp.write_text(json.dumps(cfg))
        self.sid = "sess-1"

    def start(self, title="Subtract"):
        out = self.cli("start", "-p", "demo", "--title", title, "--request", "sub fonksiyonu ekle", "--size", "S").stdout
        m = re.search(r"\[orkestra:bind\] project=(.+?) run=(\S+)", out)
        self.assertIsNotNone(m, out)
        self.rid = m.group(2)
        self.fdir = self.proj / ".orkestra" / "features" / self.rid
        assert (self.proj / ".orkestra" / "current" / "request.md").exists(), "current symlink missing"
        # bind the session the way the PostToolUse hook does in real life
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "Bash", "tool_input": {"command": "orkestra start"},
                                  "tool_response": {"stdout": out, "stderr": ""}, "cwd": str(self.proj)})
        return out

    def phase_next(self, expect_ok=True):
        cp = self.cli("phase", "next", "-p", "demo", check=False)
        if expect_ok:
            self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        else:
            self.assertNotEqual(cp.returncode, 0, cp.stdout)
        return cp

    def advance_to_approve(self):
        self.phase_next()  # → discover
        (self.fdir / "context.md").write_text("# Context\n" + ("calc.py defines add(); tests in tests/. " * 10))
        self.cli("gate", "baseline", "-p", "demo")
        self.phase_next()  # → clarify
        (self.fdir / "spec.md").write_text(SPEC)
        self.phase_next()  # → plan
        (self.fdir / "plan.md").write_text(PLAN)
        (self.fdir / "reviews" / "plan-critic.md").write_text("# critic\nVERDICT: PASS\n")
        self.phase_next()  # → approve

    def tdd_cycle(self, tid, fn, expr, x, y, want):
        """Real red → green: a new failing test, then the implementation, same command both times."""
        test = self.proj / "tests" / f"test_{fn}.py"
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Write", "cwd": str(self.proj),
                                 "tool_input": {"file_path": str(test)}})
        test.write_text("import unittest, sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
                        f"from calc import {fn}\n\nclass T(unittest.TestCase):\n    def test_{fn}(self):\n"
                        f"        self.assertEqual({fn}({x}, {y}), {want})\n")
        cmd = f"python3 -m unittest tests.test_{fn} -q"
        self.cli("tdd", "red", tid, "-p", "demo", "--", cmd)
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Edit", "cwd": str(self.proj),
                                 "tool_input": {"file_path": str(self.proj / "calc.py")}})
        with open(self.proj / "calc.py", "a") as fh:
            fh.write(f"\n\ndef {fn}(a, b):\n    return {expr}\n")
        self.cli("tdd", "green", tid, "-p", "demo", "--", cmd)

    def approve(self, text="onaylıyorum"):
        return self.hook("UserPromptSubmit", {"session_id": self.sid, "prompt": text, "prompt_id": "p-approve",
                                              "cwd": str(self.proj)})


class TestHappyPath(FlowBase):
    def test_full_lifecycle(self):
        self.start()
        self.assertTrue((self.fdir / "request.md").exists())
        # precondition failures are explicit
        self.phase_next()  # intake → discover
        cp = self.phase_next(expect_ok=False)
        self.assertIn("context.md", cp.stderr)
        self.assertIn("baseline", cp.stderr)
        (self.fdir / "context.md").write_text("# Context\n" + ("calc.py defines add(); tests in tests/. " * 10))
        out = self.cli("gate", "baseline", "-p", "demo").stdout
        self.assertIn("PASS", out)
        self.phase_next()  # → clarify
        cp = self.phase_next(expect_ok=False)
        self.assertIn("spec.md", cp.stderr)
        (self.fdir / "spec.md").write_text(SPEC)
        self.phase_next()  # → plan
        (self.fdir / "plan.md").write_text(PLAN.replace("(FR-001)", ""))
        cp = self.phase_next(expect_ok=False)
        self.assertIn("FR-001 is not covered", cp.stderr)
        self.assertIn("plan-critic", cp.stderr)
        (self.fdir / "plan.md").write_text(PLAN)
        (self.fdir / "reviews" / "plan-critic.md").write_text("VERDICT: PASS\n")
        self.phase_next()  # → approve

        # source edits are denied before approval (real hook, real binding)
        out = self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Edit", "cwd": str(self.proj),
                                       "tool_input": {"file_path": str(self.proj / "calc.py")}})
        self.assertTrue(self.denied(out), out)

        # model cannot self-approve: no approval → cannot enter implement
        cp = self.phase_next(expect_ok=False)
        self.assertIn("no user approval", cp.stderr)
        # a change request is NOT an approval
        ctx = self.approve("onaylıyorum ama T001'e bir test daha ekle")
        self.assertIn("did NOT approve", json.dumps(ctx))
        self.phase_next(expect_ok=False)
        # real approval via the user's prompt (hook-written)
        ctx = self.approve("onaylıyorum")
        self.assertIn("APPROVED", json.dumps(ctx))
        self.phase_next()  # → implement

        # spec is now frozen
        out = self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Edit", "cwd": str(self.proj),
                                       "tool_input": {"file_path": str(self.fdir / "spec.md")}})
        self.assertTrue(self.denied(out))
        # source edits now allowed and backed up before first change
        out = self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Edit", "cwd": str(self.proj),
                                       "tool_input": {"file_path": str(self.proj / "calc.py")}})
        self.assertFalse(self.denied(out))

        # TDD: done without evidence is refused
        self.cli("task", "start", "T001", "-p", "demo")
        cp = self.cli("task", "done", "T001", "-p", "demo", check=False)
        self.assertEqual(cp.returncode, 2)
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Write", "cwd": str(self.proj),
                                 "tool_input": {"file_path": str(self.proj / "tests" / "test_sub.py")}})
        (self.proj / "tests" / "test_sub.py").write_text(
            "import unittest, sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
            "from calc import sub\n\nclass T(unittest.TestCase):\n    def test_sub(self):\n        self.assertEqual(sub(5, 3), 2)\n")
        cp = self.cli("tdd", "red", "T001", "-p", "demo", "--", "python3 -m unittest tests.test_sub -q", check=False)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        # implementing
        with open(self.proj / "calc.py", "a") as fh:
            fh.write("\n\ndef sub(a, b):\n    return a - b\n")
        cp = self.cli("tdd", "green", "T001", "-p", "demo", "--", "python3 -m unittest tests.test_sub -q")
        self.cli("task", "done", "T001", "-p", "demo", "reviewed: PASS")
        self.phase_next()  # → verify

        # verify
        out = self.cli("gate", "verify", "-p", "demo").stdout
        self.assertIn("NO REGRESSIONS", out)
        cp = self.phase_next(expect_ok=False)
        self.assertIn("verification.md", cp.stderr)
        # hallucinated references are rejected
        (self.fdir / "verification.md").write_text("| FR-001 | `tests/test_ghost.py::test_x` | PASS |\n"
                                                   "| SC-001 | `test_nonexistent_case` | PASS |\n\nVERDICT: PASS\n")
        cp = self.phase_next(expect_ok=False)
        self.assertIn("does not exist", cp.stderr)
        self.assertIn("test_nonexistent_case", cp.stderr)
        (self.fdir / "verification.md").write_text("| FR-001 | `tests/test_sub.py::test_sub` | PASS |\n"
                                                   "| SC-001 | `tests/test_sub.py::test_sub` + gate unit | PASS |\n\nVERDICT: PASS\n")
        self.phase_next()  # → review

        (self.fdir / "reviews" / "triage.md").write_text("| id | k | b | v | s |\n|---|---|---|---|---|\n| R-001 | reviewer | x | fix | open |\n")
        cp = self.phase_next(expect_ok=False)
        self.assertIn("R-001", cp.stderr)
        (self.fdir / "reviews" / "triage.md").write_text("| R-001 | reviewer | x | fix | done |\n")
        self.phase_next()  # → deliver

        cp = self.phase_next(expect_ok=False)
        self.assertIn("report.md", cp.stderr)
        (self.fdir / "report.md").write_text("# Teslim raporu\n" + ("detay " * 80))
        self.cli("memory", "feature-add", "-p", "demo", "--title", "Subtract", "--summary", "sub() eklendi",
                 "--capabilities", "calc")
        cp = self.phase_next(expect_ok=False)
        self.assertIn("evidence block", cp.stderr)
        self.cli("report", "evidence", "--write", "-p", "demo")
        rep = (self.fdir / "report.md").read_text()
        self.assertIn("Makine kanıtı", rep)
        self.assertIn("| T001 done | 1 | 0 |", rep)
        # tampering with the generated block is detected
        (self.fdir / "report.md").write_text(rep.replace("| T001 done | 1 | 0 |", "| T001 done | 1 | 0 | perfect |"))
        self.phase_next(expect_ok=False)
        self.cli("report", "evidence", "--write", "-p", "demo")
        cp = self.phase_next()  # → done
        self.assertIn("[orkestra:unbind]", cp.stdout)
        self.assertIn(self.rid, (self.proj / ".orkestra" / "memory" / "FEATURES.md").read_text())

        # attribution and rollback (no git writes)
        changes = self.cli("changes", "-p", "demo", "--run", self.rid).stdout
        self.assertIn("modified  calc.py", changes)
        self.assertIn("created   tests/test_sub.py", changes)
        head_before = self.git(self.proj, "rev-parse", "HEAD")
        dry = self.cli("rollback", "-p", "demo", "--run", self.rid).stdout
        self.assertIn("DRY RUN", dry)
        self.cli("rollback", "-p", "demo", "--run", self.rid, "--yes")
        self.assertNotIn("sub", (self.proj / "calc.py").read_text())
        self.assertFalse((self.proj / "tests" / "test_sub.py").exists())
        self.assertEqual(head_before, self.git(self.proj, "rev-parse", "HEAD"))
        status = self.git(self.proj, "status", "--porcelain")
        self.assertEqual([ln for ln in status.splitlines() if not ln.endswith(".orkestra/")], [])
        # git log untouched: still exactly one commit
        self.assertEqual(len(self.git(self.proj, "log", "--oneline").splitlines()), 1)


class TestSafetyNets(FlowBase):
    def test_regression_detected(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        (self.proj / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        cp = self.cli("gate", "verify", "-p", "demo", check=False)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("REGRESSION", cp.stdout)
        self.assertIn("unit", cp.stdout)

    def test_plan_change_after_approval_requires_reapproval(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        (self.fdir / "plan.md").write_text(PLAN + "\n<!-- sneaky change -->\n")
        cp = self.phase_next(expect_ok=False)
        self.assertIn("changed after approval", cp.stderr)

    def test_phase_back_invalidates_approval(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()  # implement
        self.cli("phase", "back", "plan", "--reason", "scope change", "-p", "demo")
        st = json.loads(self.cli("status", "--json", "-p", "demo").stdout)
        self.assertIsNone(st["approved"])

    def test_dirty_baseline_is_preserved(self):
        (self.proj / "calc.py").write_text("def add(a, b):\n    return a + b  # user WIP\n")
        out = self.start()
        self.assertIn("uncommitted", out)
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        for f, tool in (("new.py", "Write"), ("calc.py", "Edit")):
            self.hook("PreToolUse", {"session_id": self.sid, "tool_name": tool, "cwd": str(self.proj),
                                     "tool_input": {"file_path": str(self.proj / f)}})
        (self.proj / "new.py").write_text("x = 1\n")
        with open(self.proj / "calc.py", "a") as fh:
            fh.write("\ndef sub(a, b):\n    return a - b\n")
        (self.proj / "users_own_note.txt").write_text("written by the user during the run\n")
        changes = self.cli("changes", "-p", "demo").stdout
        self.assertIn("calc.py", changes)
        self.assertIn("new.py", changes)
        self.cli("rollback", "-p", "demo", "--yes", env={})  # direct CLI (no hook) — user ran it
        self.assertIn("# user WIP", (self.proj / "calc.py").read_text())
        self.assertNotIn("sub", (self.proj / "calc.py").read_text())
        self.assertFalse((self.proj / "new.py").exists())
        # a file orkestra never touched is left alone by default
        self.assertTrue((self.proj / "users_own_note.txt").exists())

    def test_one_open_run_per_project(self):
        self.start()
        cp = self.cli("start", "-p", "demo", "--title", "Other", "--request", "x", check=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("already has an open run", cp.stderr)

    def test_stop_hook_blocks_mid_run_then_relents(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        ev = {"session_id": self.sid, "prompt_id": "p9", "stop_hook_active": False, "cwd": str(self.proj)}
        out = self.hook("Stop", ev)
        self.assertEqual(out.get("decision"), "block")
        self.assertEqual(self.hook("Stop", dict(ev, stop_hook_active=True)), {})
        self.hook("Stop", ev)
        self.assertEqual(self.hook("Stop", ev), {})  # capped at 2 per prompt
        self.cli("await", "-p", "demo", "Hangi renk?")
        self.assertEqual(self.hook("Stop", dict(ev, prompt_id="p10")), {})

    def test_subagent_stop_requires_green(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        self.cli("task", "start", "T001", "-p", "demo")
        ev = {"session_id": self.sid, "agent_type": "orkestra:implementer", "agent_id": "a1", "stop_hook_active": False}
        out = self.hook("SubagentStop", ev)
        self.assertEqual(out.get("decision"), "block")
        self.assertEqual(self.hook("SubagentStop", ev), {})  # once per agent
        self.assertEqual(self.hook("SubagentStop", dict(ev, agent_type="orkestra:reviewer", agent_id="a2")), {})

    def test_git_commit_needs_prompt_grant(self):
        self.start()
        ev = {"session_id": self.sid, "prompt_id": "p1", "tool_name": "Bash", "cwd": str(self.proj),
              "tool_input": {"command": "git add -A && git commit -m wip"}}
        self.hook("UserPromptSubmit", {"session_id": self.sid, "prompt_id": "p1", "prompt": "testleri koş"})
        self.assertTrue(self.denied(self.hook("PreToolUse", ev)))
        self.hook("UserPromptSubmit", {"session_id": self.sid, "prompt_id": "p2", "prompt": "commitle"})
        self.assertFalse(self.denied(self.hook("PreToolUse", dict(ev, prompt_id="p2"))))
        # grant does not leak into the next prompt
        self.hook("UserPromptSubmit", {"session_id": self.sid, "prompt_id": "p3", "prompt": "şimdi README'yi düzelt"})
        self.assertTrue(self.denied(self.hook("PreToolUse", dict(ev, prompt_id="p3"))))

    def test_askuserquestion_approval(self):
        self.start()
        self.advance_to_approve()
        resp = {"questions": [{"question": "Plan onaylıyor musun?", "header": "Plan onayı"}],
                "answers": {"Plan onaylıyor musun?": "Onayla — uygulamaya başla"}}
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "AskUserQuestion", "cwd": str(self.proj),
                                  "tool_input": {"questions": resp["questions"], "answers": resp["answers"]},
                                  "tool_response": resp})
        self.phase_next()  # approved → implement

    def test_askuserquestion_other_with_feedback_is_not_approval(self):
        self.start()
        self.advance_to_approve()
        q = [{"question": "Plan onaylıyor musun?", "header": "Plan onayı"}]
        resp = {"questions": q, "answers": {"Plan onaylıyor musun?": "Onayla ama T001'i böl"}}
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "AskUserQuestion", "cwd": str(self.proj),
                                  "tool_input": {"questions": q}, "tool_response": resp})
        self.phase_next(expect_ok=False)
        # unrelated questions in approve phase never count
        q2 = [{"question": "Renk ne olsun?", "header": "Renk"}]
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "AskUserQuestion", "cwd": str(self.proj),
                                  "tool_input": {"questions": q2},
                                  "tool_response": {"questions": q2, "answers": {"Renk ne olsun?": "Onayla — uygulamaya başla"}}})
        self.phase_next(expect_ok=False)

    def test_model_supplied_answers_in_input_are_not_trusted(self):
        self.start()
        self.advance_to_approve()
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "AskUserQuestion", "cwd": str(self.proj),
                                  "tool_input": {"answers": {"q": "Onayla"}}, "tool_response": {"note": "no answers"}})
        self.phase_next(expect_ok=False)


class TestReviewFixes(FlowBase):
    def test_gate_written_files_do_not_block_review(self):
        # a gate that writes an untracked report file on every run (junit.xml style)
        cfgp = self.proj / ".orkestra" / "config.json"
        cfg = json.loads(cfgp.read_text())
        cfg["gates"][0]["cmd"] = UNITTEST + " && date +%s%N > junit-report.txt"
        cfgp.write_text(json.dumps(cfg))
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()  # implement
        self.cli("task", "start", "T001", "-p", "demo")
        self.cli("task", "add", "FIX-R001", "extra fix", "-p", "demo")
        st = json.loads(self.cli("status", "--json", "-p", "demo").stdout)
        self.assertIn("FIX-R001", st["tasks"])
        self.tdd_cycle("T001", "sub", "a - b", 5, 3, 2)
        self.cli("task", "done", "T001", "-p", "demo")
        self.cli("task", "start", "FIX-R001", "-p", "demo")
        self.tdd_cycle("FIX-R001", "mul", "a * b", 2, 3, 6)
        self.cli("task", "done", "FIX-R001", "-p", "demo")
        # reopen resets evidence, so done is refused until a new red/green cycle
        self.cli("task", "reopen", "T001", "review round 2", "-p", "demo")
        cp = self.cli("task", "done", "T001", "-p", "demo", check=False)
        self.assertEqual(cp.returncode, 2)
        self.tdd_cycle("T001", "div", "a // b", 6, 3, 2)
        self.cli("task", "done", "T001", "-p", "demo")
        self.phase_next()  # verify
        self.cli("gate", "verify", "-p", "demo")
        (self.fdir / "verification.md").write_text("| FR-001 | `tests/test_sub.py::test_sub` | PASS |\n"
                                                   "| SC-001 | gate unit | PASS |\nVERDICT: PASS\n")
        self.phase_next()  # review — must not be blocked by junit-report.txt written during verify
        out = self.cli("gate", "last", "-p", "demo").stdout
        self.assertIn("NO REGRESSIONS", out)

    def test_quoted_tdd_command_keeps_quoting(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        self.cli("task", "start", "T001", "-p", "demo")
        cp = self.cli("tdd", "green", "T001", "-p", "demo", "--", "python3", "-c", "import sys; sys.exit(0 if 'a b' else 1)")
        self.assertIn("exit 0", cp.stdout)

    def test_serena_edits_are_backed_up_for_rollback(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "mcp__serena__replace_symbol_body", "cwd": str(self.proj),
                                 "tool_input": {"name_path": "add", "relative_path": "calc.py", "body": "x"}})
        (self.proj / "calc.py").write_text("def add(a, b):\n    return 42\n")
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "mcp__serena__replace_symbol_body", "cwd": str(self.proj),
                                  "tool_input": {"relative_path": "calc.py"}, "tool_response": "ok"})
        out = self.cli("rollback", "-p", "demo").stdout
        self.assertIn("Changed by orkestra tools", out)
        self.assertIn("calc.py: restore pre-edit copy", out)

    def test_status_does_not_bind_other_sessions(self):
        self.start()
        out = self.cli("status", "-p", "demo").stdout + self.cli("context", "-p", "demo").stdout
        self.assertNotIn("[orkestra:bind]", out)


class TestNonGit(helpers.Sandbox):
    def test_non_git_changes_and_rollback(self):
        proj = self.make_project("plain", git=False)
        self.cli("init", "-p", "plain")
        out = self.cli("start", "-p", "plain", "--title", "T", "--request", "r").stdout
        rid = re.search(r"run=(\S+)", out).group(1)
        sid = "s-ng"
        self.hook("PostToolUse", {"session_id": sid, "tool_name": "Bash", "tool_response": {"stdout": out}})
        # force phase to implement for this low-level test
        stp = proj / ".orkestra" / "runs" / rid / "state.json"
        st = json.loads(stp.read_text())
        st["phase"] = "implement"
        st["approved"] = {"deps": []}
        stp.write_text(json.dumps(st))
        target = proj / "calc.py"
        self.hook("PreToolUse", {"session_id": sid, "tool_name": "Write", "cwd": str(proj),
                                 "tool_input": {"file_path": str(target)}})
        target.write_text("broken\n")
        (proj / "extra.txt").write_text("new\n")
        ch = self.cli("changes", "-p", "plain").stdout
        self.assertIn("calc.py", ch)
        self.assertIn("extra.txt", ch)
        out = self.cli("rollback", "-p", "plain", "--yes").stdout
        self.assertIn("def add", target.read_text())
        self.assertTrue((proj / "extra.txt").exists(), out)       # not touched by orkestra tools → kept
        self.assertIn("saved before overwriting", out)
        self.cli("rollback", "-p", "plain", "--yes", "--all")
        self.assertFalse((proj / "extra.txt").exists())


class TestContextAndPlaybooks(helpers.Sandbox):
    def test_context_without_project_lists_workspace(self):
        self.make_project("alpha")
        out = self.cli("context", cwd=self.ws).stdout
        self.assertIn("alpha", out)
        self.assertIn("-p <name>", out)

    def test_context_in_project(self):
        p = self.make_project("beta")
        out = self.cli("context", cwd=p).stdout
        self.assertIn("project: beta", out)
        self.assertIn("NOT INITIALIZED", out)


if __name__ == "__main__":
    unittest.main()
