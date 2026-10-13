"""Integration of the localization work: a whole run in English (every CLI call and every hook through to the delivery
report), the same run continued in Turkish halfway, and the places where two modules meet."""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers
from test_flow import FlowBase
from test_hooks2 import Base as HookBase
from test_i18n_protocol import EN_PLAN, EN_SPEC

TR_LETTERS = re.compile("[çğıöşüÇĞİÖŞÜ]")
UNITTEST = "python3 -m unittest discover -s tests -q"


@unittest.skipUnless(os.name != "nt", "point_current symlink / POSIX flow fixtures (FR-007)")
class TestEnglishRunFromStartToDelivery(FlowBase):
    """What an English-speaking user gets on a machine with no Turkish anywhere: nothing Turkish is ever printed or
    written, and every protocol step is satisfied by the English wording alone."""

    def setUp(self):
        self.seen = []
        patcher = mock.patch.dict(os.environ, {"ORKESTRA_LANG": "en"})
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def cli(self, *args, **kw):
        cp = super().cli(*args, **kw)
        self.seen.append((" ".join(args), cp.stdout + cp.stderr))
        return cp

    def hook(self, event, payload):
        out = super().hook(event, payload)
        self.seen.append((f"hook {event}", json.dumps(out, ensure_ascii=False)))
        return out

    def ask(self, header, question, answer):
        q = [{"question": question, "header": header}]
        return self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "AskUserQuestion", "cwd": str(self.proj),
                                         "tool_input": {"questions": q},
                                         "tool_response": {"questions": q, "answers": {question: answer}}})

    def ledger(self):
        return (self.fdir / "ledger.md").read_text(encoding="utf-8")

    def test_the_whole_run_in_english(self):
        self.start(size="M", request="add a sub function")
        request = (self.fdir / "request.md").read_text(encoding="utf-8")
        self.assertIn("## Raw request", request)
        self.assertIn("- size: M", request)
        with open(self.fdir / "request.md", "a", encoding="utf-8") as fh:
            fh.write("\n## Classification\n- type: feature\n")
        self.assertIn("phase next", self.cli("next", "-p", "demo").stdout)
        self.phase_next()                                                    # intake → discover
        (self.fdir / "context.md").write_text("# Context\n" + ("calc.py defines add(); tests in tests/. " * 10),
                                              encoding="utf-8")
        self.cli("gate", "baseline", "-p", "demo")
        self.phase_next()                                                    # → clarify
        (self.fdir / "spec.md").write_text(EN_SPEC, encoding="utf-8")
        self.phase_next()                                                    # → plan
        (self.fdir / "plan.md").write_text(EN_PLAN, encoding="utf-8")
        (self.fdir / "reviews" / "plan-critic.md").write_text("# critic\nVERDICT: PASS\n", encoding="utf-8")
        self.phase_next()                                                    # → approve

        # the model cannot approve for the user, in either language
        cp = self.phase_next(expect_ok=False)
        self.assertIn("no user approval", cp.stderr)
        # the contract wording of the approval question: change request first, then the real approval
        self.ask("Plan approval", "Do you approve the spec and plan?", "I want changes")
        self.assertIn("user reject via AskUserQuestion", self.ledger())
        self.phase_next(expect_ok=False)
        self.ask("Plan approval", "Do you approve the spec and plan?", "Approve and start implementing")
        self.assertIn("user approve via AskUserQuestion", self.ledger())
        self.phase_next()                                                    # → implement
        self.assertEqual(json.loads(self.cli("status", "--json", "-p", "demo").stdout)["phase"], "implement")

        self.cli("task", "start", "T001", "-p", "demo")
        self.tdd_cycle("T001", "sub", "a - b", 5, 3, 2)
        (self.fdir / "reviews" / "T001.review.md").write_text("VERDICT: PASS\n", encoding="utf-8")
        self.cli("task", "done", "T001", "-p", "demo", "reviewed: PASS")
        self.phase_next()                                                    # → verify
        self.assertIn("NO REGRESSIONS", self.cli("gate", "verify", "-p", "demo").stdout)
        (self.fdir / "verification.md").write_text(
            "| FR-001 | `tests/test_sub.py::test_sub` | PASS |\n"
            "| SC-001 | `tests/test_sub.py::test_sub` + gate unit | PASS |\n\nVERDICT: PASS\n", encoding="utf-8")
        self.phase_next()                                                    # → review
        (self.fdir / "reviews" / "triage.md").write_text("| R-001 | reviewer | x | fix | done |\n", encoding="utf-8")
        self.phase_next()                                                    # → deliver
        (self.fdir / "report.md").write_text("# Delivery report\n" + ("details " * 80), encoding="utf-8")
        self.cli("memory", "feature-add", "-p", "demo", "--title", "Subtract", "--summary", "sub() added",
                 "--capabilities", "calc")
        self.cli("report", "evidence", "--write", "-p", "demo")
        report = (self.fdir / "report.md").read_text(encoding="utf-8")
        self.assertIn("Machine evidence", report)
        self.assertIn("| T001 done | 1 | 0 |", report)
        self.assertIn("[orkestra:unbind]", self.phase_next().stdout)         # → done
        features = (self.proj / ".orkestra" / "memory" / "FEATURES.md").read_text(encoding="utf-8")
        self.assertTrue(features.startswith("# Features"))
        self.assertIn(self.rid, features)

        for view in (("watch", "--once"), ("tempo", "-p", "demo", "--run", self.rid), ("status", "-p", "demo", "--run", self.rid)):
            self.cli(*view, check=False)
        activity = self.proj / ".orkestra" / "runs" / self.rid / "activity.jsonl"
        if activity.exists():
            self.seen.append(("activity.jsonl", activity.read_text(encoding="utf-8")))
        for name in ("request.md", "ledger.md", "report.md"):
            self.seen.append((name, (self.fdir / name).read_text(encoding="utf-8")))

        self.assertGreater(len(self.seen), 30, "the recorder saw (almost) nothing: the leak check would be vacuous")
        leaks = [(step, m.group(0), text[max(0, m.start() - 40):m.end() + 40])
                 for step, text in self.seen for m in TR_LETTERS.finditer(text)]
        self.assertEqual(leaks[:5], [], "Turkish letters in an English run")

    def test_continuing_the_same_run_in_turkish_keeps_every_artifact_valid(self):
        self.start(size="M", request="add a sub function")
        with open(self.fdir / "request.md", "a", encoding="utf-8") as fh:
            fh.write("\n## Classification\n- type: feature\n")
        self.phase_next()
        (self.fdir / "context.md").write_text("# Context\n" + ("calc.py defines add(); tests in tests/. " * 10), encoding="utf-8")
        self.cli("gate", "baseline", "-p", "demo")
        self.phase_next()
        (self.fdir / "spec.md").write_text(EN_SPEC, encoding="utf-8")
        self.phase_next()
        (self.fdir / "plan.md").write_text(EN_PLAN, encoding="utf-8")
        (self.fdir / "reviews" / "plan-critic.md").write_text("VERDICT: PASS\n", encoding="utf-8")
        self.phase_next()
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "tr"}):
            # the user switched to Turkish: the legacy Turkish option label approves, the same state is shown in Turkish
            self.ask("Plan onayı", "Plan ve spec'i onaylıyor musun?", "Onayla — uygulamaya başla")
            self.assertIn("user approve via AskUserQuestion", self.ledger())
            self.phase_next()
            self.assertIn("implement", self.cli("next", "-p", "demo").stdout)
            watch = self.cli("watch", "--once").stdout
            self.assertRegex(watch, r"faz|durum")
        self.assertEqual(json.loads(self.cli("status", "--json", "-p", "demo").stdout)["phase"], "implement")


class TestWhereTwoModulesMeet(HookBase):
    Q_APPR = {"question": "Do you approve the spec and plan?", "header": "Plan approval"}
    Q_EXTRA = {"question": "Which extra scope should join this part?", "header": "Extra scope", "multiSelect": True}

    def answer_without_labels(self, lang):
        """Both answers arrive as bare strings (no question text): the approval is picked by position."""
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": lang}):
            self.start()
            self.advance_to_approve()
            out = self.hook("PostToolUse", {
                "session_id": self.sid, "tool_name": "AskUserQuestion", "cwd": str(self.proj),
                "tool_input": {"questions": [self.Q_APPR, self.Q_EXTRA]},
                "tool_response": {"answers": ["Approve and start implementing", "GT-R caliper glow fix"]}})
        return json.dumps(out, ensure_ascii=False), self.ledger()

    def check_label(self, lang):
        """The label is part of text the model reads (and of the approval note): it must not be Turkish."""
        ctx, ledger = self.answer_without_labels(lang)
        self.assertIn("extra question = GT-R caliper glow fix", ctx)
        self.assertNotIn("ek soru", ctx + ledger)
        self.assertIn("user approve via AskUserQuestion", ledger)
        self.assertIn("NOT part of the approval", ctx)

    def test_unlabelled_extra_answer_gets_an_english_label_in_an_english_session(self):
        self.check_label("en")

    def test_unlabelled_extra_answer_gets_an_english_label_in_a_turkish_session(self):
        self.check_label("tr")


class TestDoctorChecksThePythonTheHooksStart(helpers.Sandbox):
    """hooks/hooks.json starts `python3`; where that name does not resolve (Windows) the guards silently do not run."""

    def doctor(self, path_dirs):
        return self.cli("doctor", check=False, env={"PATH": os.pathsep.join(str(d) for d in path_dirs),
                                                    "ORKESTRA_LANG": "en"}).stdout

    def test_row_is_informational_and_follows_the_path(self):
        empty = self.tmp / "empty-bin"
        empty.mkdir()
        self.assertIn("[--] python3 on PATH (plugin hooks)", self.doctor([empty]))
        shim = self.tmp / "shim-python"
        shim.mkdir()
        (shim / ("python3.exe" if os.name == "nt" else "python3")).write_text("", encoding="utf-8")
        if os.name != "nt":
            os.chmod(shim / "python3", 0o755)
        self.assertIn("[OK] python3 on PATH (plugin hooks)", self.doctor([shim]))

    def test_the_message_names_the_real_fallbacks_of_the_launcher(self):
        """run_hook.sh tries python3, python and `py -3`: a PATH with only `python` must not be told to make a python3."""
        only_python = self.tmp / "only-python"
        only_python.mkdir()
        exe = only_python / ("python.exe" if os.name == "nt" else "python")
        exe.write_text("", encoding="utf-8")
        if os.name != "nt":
            os.chmod(exe, 0o755)
        row = next(ln for ln in self.doctor([only_python]).splitlines() if "python3 on PATH" in ln)
        self.assertTrue(row.startswith("[--]"), row)
        self.assertIn("falls back to", row)
        self.assertIn(str(exe), row)
        self.assertNotIn("python3.exe", row)
        none = next(ln for ln in self.doctor([self.tmp / "nowhere"]).splitlines() if "python3 on PATH" in ln)
        self.assertIn("neither `python` nor `py`", none)
        self.assertNotIn("python3.exe", none)

    def test_a_missing_python3_never_fails_the_doctor_on_its_own(self):
        out = self.doctor([self.tmp / "nowhere"])
        row = next(ln for ln in out.splitlines() if "python3 on PATH" in ln)
        self.assertTrue(row.startswith("[--]"), row)           # soft row: `[!!]` is reserved for required checks


class TestWindowsPathShim(helpers.Sandbox):
    """`orkestra.cmd` is written on every Python (3.9 has no `Path.write_text(newline=)`) with exactly one CR per line."""

    def test_cmd_shim_is_byte_exact_crlf(self):
        from orkestra_core import cursorkit as CK
        shim_dir = self.tmp / "win-shim"
        shim_dir.mkdir()
        env = {"ORKESTRA_SHIM_DIR": str(shim_dir), "PATH": str(Path(sys.executable).parent)}
        with mock.patch.dict(os.environ, env), mock.patch.object(CK, "_is_windows", return_value=True):
            actions = CK.ensure_path_shim()
            self.assertEqual(CK.ensure_path_shim(), [])                 # idempotent
        data = (shim_dir / "orkestra.cmd").read_bytes()
        self.assertTrue(any("orkestra.cmd" in a for a in actions))
        self.assertTrue(data.startswith(b"@echo off\r\n"))
        self.assertNotIn(b"\r\r\n", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "every line ends in CRLF, none in a bare LF")
        self.assertIn(sys.executable.encode("utf-8"), data)
        self.assertIn(b"%*", data)


class TestDocumentsAgreeWithTheCode(unittest.TestCase):
    ROOT = helpers.PLUGIN.parent.parent

    def read(self, name):
        return (self.ROOT / name).read_text(encoding="utf-8")

    def test_both_readmes_document_the_language_switches(self):
        for name in ("README.md", "README.tr.md"):
            text = self.read(name)
            for needle in ("ORKESTRA_LANG", "orkestra lang show", "--lang", "config.json", "/yap", "/run", "phase_label"):
                self.assertIn(needle, text, f"{name} must mention {needle}")
        self.assertIn("ORKESTRA.tr.md", self.read("README.md"))
        self.assertIn("ORKESTRA.tr.md", self.read("README.tr.md"))
        self.assertIn("English is the default", self.read("README.md"))
        self.assertIn("varsayılan İngilizcedir", self.read("README.tr.md"))

    def test_resolution_order_in_the_readmes_matches_i18n(self):
        """README lists the sources in the order i18n.lang applies them: flag, env, config, OS locale, English."""
        for name in ("README.md", "README.tr.md"):
            text = self.read(name)
            at = [text.index(token) for token in ("`--lang en|tr|auto`", "`ORKESTRA_LANG`", "`lang`", "`LC_ALL`")]
            self.assertEqual(at, sorted(at), name)

    def test_changelog_announces_the_language_work(self):
        text = self.read("CHANGELOG.md")
        unreleased = text.split("\n## ", 2)[1]
        # The Unreleased entry is written in English (the Turkish entries stop at 1.6.0, and test_docs_drift forbids Turkish
        # letters in this section), so the announcement is checked by its English words.
        for needle in ("English and Turkish", "orkestra lang", "ORKESTRA_LANG"):
            self.assertIn(needle, unreleased)


if __name__ == "__main__":
    unittest.main()
