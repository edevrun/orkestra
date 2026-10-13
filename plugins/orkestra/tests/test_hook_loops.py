"""Hook layer: deadline-safe and loop-proof. One hook-wide deadline that nothing can swallow, git facts only when a real
git write is typed, a Stop-nudge allowance keyed by progress, parallel implementers that do not nudge each other, an
escalation ladder for identical refusals, lifecycle signals (failure, notification, session end) and the strictness of
variable write targets."""
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from helpers import DISPATCH
from test_flow import FlowBase
from test_engines import CursorBase

sys.path.insert(0, str(DISPATCH.parent))
import dispatch as D  # noqa: E402
from orkestra_core import activity, state as ST, tempo  # noqa: E402


def run_main(name: str, payload: dict):
    """dispatch.main() in this process with a payload on stdin; returns (stdout text, exit code)."""
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["dispatch.py", name]), \
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), mock.patch.object(sys, "stdout", out), \
            mock.patch.object(D, "utf8_stdio", lambda: None):
        code = D.main()
    return out.getvalue(), code


class TestHookDeadline(unittest.TestCase):
    def setUp(self):
        # deadline denials are counted (LT-2): keep the counter file out of the developer's real ORKESTRA_HOME
        import shutil
        import tempfile
        self._home = tempfile.mkdtemp(prefix="orkestra-deadline-")
        patcher = mock.patch.dict(os.environ, {"ORKESTRA_HOME": self._home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self._home, True)

    def test_deadline_is_not_an_exception(self):
        self.assertFalse(issubclass(D._Deadline, Exception))

    def test_except_exception_cannot_swallow_the_cpu_deadline(self):
        def busy():
            end = time.monotonic() + 8
            while time.monotonic() < end:
                try:
                    sum(range(2000))
                except Exception:
                    pass
            return "survived"
        with self.assertRaises(D._Deadline) as cm:
            D.run_with_hook_deadline(busy, cpu=0.3, wall=6)
        self.assertEqual(cm.exception.kind, "cpu")

    def test_a_busy_machine_is_not_a_complex_command(self):
        # waiting (no CPU) longer than the CPU budget is fine; only the wall-clock hard stop ends it
        self.assertEqual(D.run_with_hook_deadline(lambda: time.sleep(0.6) or "ok", cpu=0.2, wall=5), "ok")
        with self.assertRaises(D._Deadline) as cm:
            D.run_with_hook_deadline(lambda: time.sleep(5), cpu=3, wall=0.4)
        self.assertEqual(cm.exception.kind, "wall")

    def test_timers_are_disarmed_after_the_run(self):
        import signal
        D.run_with_hook_deadline(lambda: None, cpu=0.5, wall=0.5)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0.0)
        time.sleep(0.7)                                  # a stale timer would raise here

    def test_pre_tool_use_is_refused_with_a_retry_text_at_the_wall_deadline(self):
        slow = {"PreToolUse": lambda ev: time.sleep(5)}
        with mock.patch.dict(D.HANDLERS, slow), mock.patch.object(D, "HOOK_WALL_SECONDS", 0.4):
            out, code = run_main("PreToolUse", {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(code, 0)
        res = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(res["permissionDecision"], "deny")
        self.assertIn("busy", res["permissionDecisionReason"])
        self.assertIn("Retry the same call once", res["permissionDecisionReason"])

    def test_other_events_end_quietly_at_the_deadline(self):
        slow = {"PostToolUse": lambda ev: time.sleep(5)}
        with mock.patch.dict(D.HANDLERS, slow), mock.patch.object(D, "HOOK_WALL_SECONDS", 0.4):
            out, code = run_main("PostToolUse", {"session_id": "s", "tool_name": "Bash"})
        self.assertEqual((out, code), ("", 0))

    def test_a_cpu_bound_guard_is_still_refused_as_too_complex(self):
        def spin(ev):
            end = time.monotonic() + 8
            while time.monotonic() < end:
                try:
                    sum(range(5000))
                except Exception:
                    pass
        with mock.patch.dict(D.HANDLERS, {"PreToolUse": spin}), mock.patch.object(D, "HOOK_CPU_SECONDS", 0.3):
            out, _ = run_main("PreToolUse", {"session_id": "s", "tool_name": "Bash"})
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("[orkestra:complex]", reason)

    def test_no_second_answer_when_the_hook_already_denied(self):
        def answer_then_hang(ev):
            D.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "[orkestra:x] y"}})
            time.sleep(5)
        with mock.patch.dict(D.HANDLERS, {"PreToolUse": answer_then_hang}), mock.patch.object(D, "HOOK_WALL_SECONDS", 0.3):
            out, _ = run_main("PreToolUse", {"session_id": "s", "tool_name": "Bash"})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"], "[orkestra:x] y")


class TestGitContextCost(helpers.Sandbox):
    def ev(self, cmd, cwd):
        return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd)}

    def count(self, cmd, cwd):
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout="main\n")
        with mock.patch("subprocess.run", fake):
            ctx = D.git_context(self.ev(cmd, cwd))
        return ctx, calls

    def test_words_that_are_not_git_writes_cost_nothing(self):
        proj = self.make_project("g1")
        for cmd in ("grep -rn switch src", "npm test -- push", "git status"):
            ctx, calls = self.count(cmd, proj)
            self.assertEqual((ctx, calls), ({}, []), cmd)

    def test_push_reads_the_branch_from_head_without_a_process(self):
        proj = self.make_project("g2")
        self.git(proj, "checkout", "-q", "-b", "feature/x")
        ctx, calls = self.count("git push origin feature/x", proj)
        self.assertEqual(ctx, {"current_branch": "feature/x"})
        self.assertEqual(calls, [])

    def test_checkout_asks_git_three_times(self):
        proj = self.make_project("g3")
        ctx, calls = self.count("cd x && git checkout -b y", proj)
        self.assertEqual(len(calls), 2 if "current_branch" in ctx else 3)       # branch from HEAD, list, status
        self.assertIn("branches", ctx)
        self.assertIn("clean", ctx)

    def test_a_slow_git_is_cut_off_by_the_shared_budget(self):
        proj = self.make_project("g4")
        clock = [0.0]

        def slow(argv, **kw):
            clock[0] += 2.0                             # each call "takes" two seconds
            return mock.Mock(returncode=0, stdout="main\n")
        with mock.patch("subprocess.run", slow), mock.patch.object(D.time, "monotonic", lambda: clock[0]):
            ctx = D.git_context(self.ev("git switch other", proj))
        self.assertEqual(ctx, {})                       # out of budget: the guards take their strict path

    def test_main_push_is_still_denied_and_feature_push_needs_a_grant(self):
        proj = self.make_project("g5")
        pre = {"session_id": "s", "tool_name": "Bash", "cwd": str(proj), "tool_input": {"command": "git push --force"}}
        self.assertTrue(self.denied(self.hook("PreToolUse", pre)))


class TestStopBudget(FlowBase):
    def setUp(self):
        super().setUp()

    def stop(self):
        return self.hook("Stop", {"session_id": self.sid, "stop_hook_active": False, "cwd": str(self.proj)})

    def set_state(self, **kw):
        p = ST.state_path(self.proj, self.rid)
        st = json.loads(p.read_text())
        st.update(kw)
        p.write_text(json.dumps(st))

    def blocked(self, out):
        return out.get("decision") == "block"

    def to_run(self, mode="manual"):
        self.start(approval_mode=mode)
        self.drive_turn()

    def test_without_a_prompt_id_two_nudges_per_state_then_allow(self):
        self.to_run()
        self.assertEqual([self.blocked(self.stop()) for _ in range(3)], [True, True, False])

    def test_real_progress_earns_new_nudges_and_none_after_it_stalls(self):
        self.to_run()
        for _ in range(3):
            self.stop()
        self.set_state(tasks={"T001": {"status": "done"}})
        self.assertEqual([self.blocked(self.stop()) for _ in range(3)], [True, True, False])
        keys = [k for k in ST.load_state(self.proj, self.rid)["stop_blocks"] if k != "_nudges"]
        self.assertEqual(len(keys), 2)
        self.assertFalse(any(k.startswith("noprompt") for k in keys))

    def test_rolling_ceiling_stops_nudges_whatever_the_progress(self):
        self.to_run()
        seen = []
        for i in range(8):
            self.set_state(tasks={f"T{n:03d}": {"status": "done"} for n in range(i + 1)})
            seen.append(self.blocked(self.stop()))
        self.assertEqual(seen.count(True), D.STOP_NUDGE_CEILING)
        self.assertEqual(seen[:6], [True] * 6)
        self.assertLessEqual(len(ST.load_state(self.proj, self.rid)["stop_blocks"]["_nudges"]), D.STOP_NUDGE_CEILING)

    def test_ceiling_forgets_old_nudges(self):
        self.to_run()
        old = time.time() - D.STOP_NUDGE_WINDOW - 60
        self.set_state(stop_blocks={"_nudges": [old] * 6})
        self.assertTrue(self.blocked(self.stop()))

    def test_distinct_prompt_ids_without_progress_are_bounded_by_the_ceiling(self):
        self.to_run()
        outs = [self.hook("Stop", {"session_id": self.sid, "prompt_id": f"p{i}", "stop_hook_active": False,
                                   "cwd": str(self.proj)}) for i in range(8)]
        self.assertEqual(sum(self.blocked(o) for o in outs), 6)

    def test_nudge_tail_for_a_user_present_run_keeps_the_question_advice(self):
        self.to_run("manual")
        self.assertIn("AskUserQuestion", self.stop()["reason"])

    def test_nudge_tail_in_autopilot_never_advises_asking(self):
        self.to_run("autopilot")
        reason = self.stop()["reason"]
        self.assertNotIn("AskUserQuestion", reason)
        self.assertNotIn("orkestra await", reason)
        self.assertIn("orkestra block", reason)
        self.assertIn("task defer", reason)


class TestSubagentStopScope(FlowBase):
    def setUp(self):
        super().setUp()
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        p = ST.state_path(self.proj, self.rid)
        st = json.loads(p.read_text())
        st["tasks"] = {"T001": {"status": "in_progress", "tdd": "required", "green": {"ok": True}},
                       "T002": {"status": "in_progress", "tdd": "required"}}
        p.write_text(json.dumps(st))

    def launch(self, agent_id, task):
        ti = {"subagent_type": "orkestra:implementer", "description": f"Implement {task}", "prompt": f"scope: task {task}"}
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Agent", "tool_input": ti, "cwd": str(self.proj)})
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "Agent", "tool_input": ti, "cwd": str(self.proj),
                                  "tool_response": {"status": "async_launched", "agentId": agent_id,
                                                    "description": ti["description"], "outputFile": "/tmp/x.output"}})

    def sub_stop(self, agent_id):
        return self.hook("SubagentStop", {"session_id": self.sid, "agent_type": "orkestra:implementer",
                                          "agent_id": agent_id, "stop_hook_active": False, "cwd": str(self.proj)})

    def test_an_implementer_is_not_nudged_for_another_ones_task(self):
        self.launch("agentA", "T001")
        self.launch("agentB", "T002")
        self.assertEqual(self.sub_stop("agentA"), {})
        out = self.sub_stop("agentB")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("T002", out["reason"])
        self.assertNotIn("T001", out["reason"])

    def test_an_unregistered_agent_keeps_the_old_run_wide_check(self):
        out = self.sub_stop("never-registered")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("T002", out["reason"])


class TestDenyEscalation(FlowBase):
    def setUp(self):
        super().setUp()
        self.start(approval_mode="autopilot")

    def ask(self, q="Which colour?"):
        return self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "AskUserQuestion", "cwd": str(self.proj),
                                        "tool_input": {"questions": [{"question": q, "header": "h"}]}})

    def reason(self, out):
        self.assertTrue(self.denied(out))
        return out["hookSpecificOutput"]["permissionDecisionReason"]

    def status(self):
        return json.loads(self.cli("status", "--json", "-p", "demo").stdout)["status"]

    def rows(self):
        p = self.proj / ".orkestra" / "runs" / self.rid / "activity.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def test_second_denial_adds_a_note_third_is_short_and_final(self):
        r = [self.reason(self.ask()) for _ in range(3)]
        self.assertIn("Record a conservative assumption", r[0])
        self.assertNotIn("denied", r[0])
        self.assertTrue(r[1].startswith(r[0]))
        self.assertIn("denied 2x", r[1])
        self.assertIn("denied 3x", r[2])
        self.assertIn("do not retry", r[2])
        self.assertLess(len(r[2]), len(r[1]) + 120)
        self.assertIn("conservative assumption", r[2])      # the guard knows the alternative

    def test_eighth_identical_denial_blocks_the_run_and_the_stop_hook_stays_quiet(self):
        for i in range(7):
            self.ask()
            self.assertEqual(self.status(), "active", i)
        last = self.reason(self.ask())
        self.assertEqual(self.status(), "blocked")
        self.assertIn("now blocked", last)
        st = json.loads(self.cli("status", "--json", "-p", "demo").stdout)
        self.assertIn("approval x8", (st.get("blocked") or {}).get("reason", ""))
        self.assertTrue(any(r["kind"] == "loop" for r in self.rows()))
        self.assertEqual(self.hook("Stop", {"session_id": self.sid, "stop_hook_active": False, "cwd": str(self.proj)}), {})

    def test_a_different_call_is_not_counted_with_the_loop(self):
        for _ in range(4):
            self.ask("A?")
        other = self.reason(self.ask("B?"))
        self.assertNotIn("denied", other)

    def test_deny_rows_carry_the_short_key(self):
        self.ask()
        row = [r for r in self.rows() if r["kind"] == "deny"][-1]
        self.assertEqual(len(row["key"]), 8)
        self.assertEqual(row["key"], D.deny_key("approval", {"tool_name": "AskUserQuestion", "tool_input": {
            "questions": [{"question": "Which colour?", "header": "h"}]}}))

    def test_the_counter_file_is_bounded(self):
        path = self.proj / ".orkestra" / "runs" / self.rid / "deny-counts.json"
        for i in range(D.DENY_MAX_KEYS + 30):
            D.bump_denial(path, f"k{i}:main", 1000.0 + i)
        self.assertEqual(len(json.loads(path.read_text())["keys"]), D.DENY_MAX_KEYS)

    def test_internals_python_path_form_gets_the_exact_rewrite(self):
        ev = {"tool_name": "Bash", "tool_input": {"command": "python3 /x/plugins/orkestra/bin/orkestra next -p demo"}}
        alt = D.deny_alternative(mock.Mock(guard="internals"), ev, None)
        self.assertEqual(alt, "run exactly: orkestra next -p demo")

    def test_guards_decide_exactly_as_before(self):
        # the ladder only rewrites text: the 20th attempt is refused just like the 1st
        outs = [self.ask() for _ in range(10)]
        self.assertTrue(all(self.denied(o) for o in outs))


class TestLifecycleHooks(FlowBase):
    def setUp(self):
        super().setUp()
        self.start()

    def rows(self):
        return activity.read_rows(self.proj, self.rid)

    def pre_bash(self, cmd="sleep 1"):
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Bash", "cwd": str(self.proj),
                                 "tool_input": {"command": cmd}})

    def test_every_event_in_hooks_json_has_a_handler(self):
        cfg = json.loads((DISPATCH.parent / "hooks.json").read_text())["hooks"]
        self.assertTrue({"PostToolUseFailure", "Notification", "StopFailure", "SessionEnd"} <= set(cfg))
        self.assertEqual(sorted(set(cfg) - set(D.HANDLERS)), [])

    def test_failed_bash_call_closes_the_running_command(self):
        self.pre_bash()
        self.assertEqual(len(tempo._running_commands(self.rows())), 1)
        self.hook("PostToolUseFailure", {"session_id": self.sid, "tool_name": "Bash", "tool_input": {"command": "sleep 1"},
                                         "error": "Command failed", "is_interrupt": False, "cwd": str(self.proj)})
        self.assertEqual(tempo._running_commands(self.rows()), [])

    def test_interrupt_is_flagged_and_closes_the_command(self):
        self.pre_bash()
        self.hook("PostToolUseFailure", {"session_id": self.sid, "tool_name": "Bash", "tool_input": {"command": "sleep 1"},
                                         "error": "aborted", "is_interrupt": True, "cwd": str(self.proj)})
        self.assertEqual(tempo._running_commands(self.rows()), [])
        last = self.rows()[-1]
        self.assertEqual(last["kind"], "end")
        self.assertIn("interrupted" if os.environ.get("ORKESTRA_LANG") == "en" else "kesildi", last["what"])

    def test_failure_payload_without_optional_fields_is_tolerated(self):
        self.hook("PostToolUseFailure", {"session_id": self.sid})
        self.hook("PostToolUseFailure", {})
        self.hook("Notification", {})
        self.hook("StopFailure", {})
        self.hook("SessionEnd", {})

    def test_permission_prompt_becomes_a_perm_wait_row_and_idle_prompt_an_idle_row(self):
        self.hook("Notification", {"session_id": self.sid, "notification_type": "permission_prompt",
                                   "message": "Claude needs your permission to use Bash"})
        self.assertEqual(self.rows()[-1]["kind"], "perm_wait")
        self.assertIn("Bash", self.rows()[-1]["what"])
        self.hook("Notification", {"session_id": self.sid, "notification_type": "idle_prompt", "message": "idle"})
        self.assertEqual(self.rows()[-1]["kind"], "idle")
        n = len(self.rows())
        self.hook("Notification", {"session_id": self.sid, "notification_type": "auth_success", "message": "ok"})
        self.assertEqual(len(self.rows()), n)

    def test_new_row_kinds_are_asides_never_progress(self):
        for kind in ("perm_wait", "api_error", "loop"):
            self.assertIn(kind, activity.ASIDE_KINDS)
            self.assertNotIn(kind, activity.DRIVE_KINDS)

    def test_stop_failure_leaves_an_api_error_row(self):
        self.hook("StopFailure", {"session_id": self.sid, "error": "rate_limit", "error_details": "429"})
        row = self.rows()[-1]
        self.assertEqual(row["kind"], "api_error")
        self.assertIn("rate_limit", row["what"])

    def test_session_end_expires_the_lease_but_keeps_the_binding(self):
        self.hook("SessionEnd", {"session_id": self.sid, "reason": "prompt_input_exit"})
        self.assertIsNotNone(ST.session_binding(self.sid))
        age = ST.run_sessions(self.proj, self.rid)[0]["age"]
        self.assertGreaterEqual(age, 120)
        self.assertEqual(self.rows()[-1]["kind"], "idle")

    def test_session_end_of_a_closed_run_drops_the_binding(self):
        self.cli("abort", "-p", "demo", "weg")
        self.hook("SessionEnd", {"session_id": self.sid, "reason": "other"})
        self.assertIsNone(ST.session_binding(self.sid))

    def test_a_session_without_a_run_costs_nothing(self):
        for ev in ("PostToolUseFailure", "Notification", "StopFailure", "SessionEnd"):
            self.assertEqual(self.hook(ev, {"session_id": "nobody", "notification_type": "permission_prompt"}), {})


class TestCursorTurnEnd(CursorBase):
    def test_an_aborted_or_failed_stop_leaves_a_row_and_no_followup(self):
        self.start()
        self.say("devam", gen="g1")
        for status, kind in (("aborted", "idle"), ("error", "api_error")):
            self.assertEqual(self.chook("stop", gen="g1", status=status, loop_count=0), {})
            self.assertEqual(activity.read_rows(self.proj, self.rid)[-1]["kind"], kind, status)


class TestVariableWriteTargets(FlowBase):
    def setUp(self):
        super().setUp()
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()

    def bash(self, cmd, **kw):
        return self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Bash", "cwd": str(self.proj),
                                        "tool_input": {"command": cmd}, **kw})

    def reason(self, out):
        return (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")

    def test_unassigned_variable_target_is_denied_with_an_actionable_text(self):
        out = self.bash("cp calc.py $DEST/b.py")
        self.assertTrue(self.denied(out))
        self.assertIn("literal path", self.reason(out))
        self.assertTrue(self.denied(self.bash("echo hi > ${OUT}/log.txt")))
        self.assertTrue(self.denied(self.bash("touch `mktemp -d`/x")))

    def test_temp_variables_and_literal_paths_stay_allowed(self):
        self.assertFalse(self.denied(self.bash("mkdir -p $TMPDIR/x")))
        self.assertFalse(self.denied(self.bash("cp calc.py ${TMPDIR:-/tmp}/b.py")))
        self.assertFalse(self.denied(self.bash(f"cp calc.py {self.proj}/b.py")))

    def test_a_variable_assigned_to_a_literal_inside_the_project_is_allowed(self):
        self.assertFalse(self.denied(self.bash("DEST=build; mkdir -p $DEST/x")))

    def test_assigned_variable_pointing_outside_is_still_denied_as_isolation(self):
        out = self.bash("DEST=/etc/orkestra-x; cp calc.py $DEST/b.py")
        self.assertTrue(self.denied(out))
        self.assertIn("[orkestra:isolation]", self.reason(out))

    def test_use_before_assignment_is_not_an_assignment(self):
        self.assertTrue(self.denied(self.bash("cp calc.py $DEST/b.py; DEST=build")))

    def test_without_a_run_nothing_changes(self):
        out = subprocess.run([sys.executable, str(DISPATCH), "PreToolUse"], capture_output=True, text=True,
                             input=json.dumps({"session_id": "nobody", "tool_name": "Bash", "cwd": str(self.proj),
                                               "tool_input": {"command": "cp calc.py $DEST/b.py"}})).stdout
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()


class TestDoctorSelfTestLeavesNoCounter(unittest.TestCase):
    def test_the_doctor_session_never_gets_a_counter_file(self):
        self.assertIsNone(D._deny_counts_path({"session_id": "doctor"}, None))
        with mock.patch.object(D.paths, "orkestra_home", return_value=Path("/nonexistent-home")):
            self.assertEqual(D._deny_counts_path({"session_id": "abc"}, None),
                             Path("/nonexistent-home") / "denies" / "abc.json")   # real sessions are still counted
