"""What the public-release audit (2026-10-06) found that a weak orchestrator could get wrong: closing a task with
no review, loading the wrong playbook, ending a headless turn under its subagents, a fix round without the review.
Plus the files a public repository must ship (licences, notices, no private data)."""
import json
import os
import re
import subprocess
import time
import unittest
from pathlib import Path

import helpers
from test_tempo import TempoBase

ROOT = helpers.PLUGIN.parent.parent


class TestTaskReviewIsEnforced(TempoBase):
    def ready(self):
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        self.tdd_cycle("T001", "sub", "a - b", 5, 3, 2)

    def done(self):
        return self.cli("task", "done", "T001", "-p", "demo", "review: PASS", check=False)

    def test_m_run_needs_a_pass_review_of_this_round(self):
        self.ready()
        cp = self.done()
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("no review of this round", cp.stderr + cp.stdout)
        self.assertIn("scope: task T001", cp.stderr + cp.stdout)
        (self.fdir / "reviews" / "T001.review.md").write_text("# review\nlooks fine\n")
        self.assertIn("has no `VERDICT:", self.done().stderr)
        self.review("T001", "FAIL")
        cp = self.done()
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("the review says FAIL", cp.stderr)
        self.assertIn("orkestra task reopen T001", cp.stderr)
        # the FAIL is applied by a reopen: the same file (even rewritten to PASS in place, older than now) is stale
        self.cli("task", "reopen", "T001", "review round 1", "-p", "demo")
        brief = self.cli("brief", "T001", "-p", "demo").stdout
        self.assertIn("## Review to fix", brief)
        self.assertIn("T001.review.md", brief)
        self.cli("task", "start", "T001", "-p", "demo")
        self.tdd_cycle("T001", "sub2", "a - b", 9, 3, 6)
        self.assertIn("no review of this round", self.done().stderr)
        time.sleep(0.05)
        self.review("T001", "PASS_WITH_NOTES", minors=1)
        self.assertEqual(self.done().returncode, 0)
        self.assertNotIn("## Review to fix", self.cli("brief", "T001", "-p", "demo").stdout)

    def test_adhoc_tasks_and_the_switch(self):
        self.ready()
        self.cli("task", "add", "FIX-R001", "extra fix", "--no-tdd", "-p", "demo")
        self.cli("task", "start", "FIX-R001", "-p", "demo", "--force", "test", check=False)
        self.assertEqual(self.cli("task", "done", "FIX-R001", "-p", "demo", check=False).returncode, 0)
        self.assertNotEqual(self.done().returncode, 0)
        self.home.mkdir(parents=True, exist_ok=True)
        cfgp = self.home / "config.json"
        cfg = json.loads(cfgp.read_text()) if cfgp.exists() else {}
        cfg.setdefault("tempo", {})["task_review_required"] = False
        cfgp.write_text(json.dumps(cfg))
        self.assertEqual(self.done().returncode, 0)


class TestSmallRunsAreNotReviewedPerTask(TempoBase):
    size = "S"

    def test_s_run(self):
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        self.tdd_cycle("T001", "sub", "a - b", 5, 3, 2)
        self.assertEqual(self.cli("task", "done", "T001", "-p", "demo", check=False).returncode, 0)


class TestPlaybookKnowsThePhase(TempoBase):
    def test_no_argument_is_the_current_phase(self):
        cp = self.cli("playbook", cwd=self.proj, check=False)
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("no open run here", cp.stderr)
        self.assertIn("headless", self.cli("playbook", "headless", cwd=self.proj).stdout.lower())      # needs no run
        self.start()
        intake = self.cli("playbook", "-p", "demo").stdout
        self.assertEqual(intake, self.cli("playbook", "intake", "-p", "demo").stdout)
        self.assertNotIn("NOTE: run", intake)
        wrong = self.cli("playbook", "implement", "-p", "demo").stdout
        self.assertTrue(wrong.startswith("NOTE: run"), wrong[:80])
        self.assertIn("is in phase `intake`, not `implement`", wrong)
        self.assertNotIn("NOTE: run", self.cli("playbook", "security", "-p", "demo").stdout)           # not a phase
        self.assertNotIn("HEADLESS:", intake)

    def test_a_headless_run_is_told_how_to_wait(self):
        self.start()
        out = self.cli("playbook", "-p", "demo", env={"ORKESTRA_HEADLESS": "1"}).stdout
        self.assertTrue(out.startswith("HEADLESS: "), out[:80])
        self.assertIn("sleep 30; orkestra next", out)
        self.assertIn("do not end your turn", (helpers.PLUGIN / "skills/run/references/headless.md").read_text().lower())


class TestPublicRepository(unittest.TestCase):
    TEXT = (".md", ".py", ".js", ".json", ".html", ".sh", ".yml", ".txt")

    def files(self):
        for d, dirs, names in os.walk(ROOT):
            root = Path(d)
            dirs[:] = [
                name for name in dirs
                if name not in ("__pycache__", ".git", "node_modules")
                and not (root == ROOT and name in ("dist", "internal"))
            ]
            for n in names:
                if n.endswith(self.TEXT):
                    yield Path(d) / n

    def test_every_vendored_project_ships_its_licence_and_is_listed(self):
        vendor = helpers.PLUGIN / "vendor"
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        pinned = (vendor / "PINNED.md").read_text(encoding="utf-8")
        dirs = sorted(p.name for p in vendor.iterdir() if p.is_dir())
        self.assertGreaterEqual(len(dirs), 9)
        for d in dirs:
            lic = vendor / d / "LICENSE"
            self.assertTrue(lic.is_file() and lic.stat().st_size > 200, f"vendor/{d} has no LICENSE")
            self.assertIn(f"| `{d}` |", notices, f"vendor/{d} missing from THIRD_PARTY_NOTICES.md")
            self.assertIn(f"| {d} |", pinned, f"vendor/{d} missing from PINNED.md")
            row = next(ln for ln in notices.splitlines() if ln.startswith(f"| `{d}` |"))
            self.assertRegex(row, r"https://github\.com/\S+ \| `[0-9a-f]{7}` \| (MIT|Apache-2\.0)")
            if "Apache" in row or "Apache" in lic.read_text(encoding="utf-8")[:200]:
                full = vendor / d / "LICENSE-APACHE-2.0.txt"
                self.assertIn("TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION", full.read_text(encoding="utf-8"))
        for holder in ("Jesse Vincent", "GitHub, Inc.", "BMad Code, LLC", "Open GSD", "OpenSpec Contributors",
                       "humanlayer Authors", "Every", "Affaan Mustafa"):
            self.assertIn(holder, notices)
        self.assertEqual((helpers.PLUGIN / "LICENSE").read_text(), (ROOT / "LICENSE").read_text())
        self.assertTrue((helpers.PLUGIN / "THIRD_PARTY_NOTICES.md").is_file())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("THIRD_PARTY_NOTICES.md", readme)
        self.assertIn("Apache-2.0", readme)

    def test_nothing_private_is_published(self):
        bad = re.compile(r"[\w.+-]+@(gmail|hotmail|outlook|yahoo|icloud)\.\w+|/Users/[a-z][\w.-]+/|/home/[a-z][\w.-]+/"
                         r"|\bchat_id=\d{6,}|\b\d{8,10}:[\w-]{30,}\b")
        allowed = ("/Users/alex/", "/Users/me/", "/Users/you/", "/Users/x/", "/home/user/", "/home/claude/", "/home/runner/")
        hits = []
        for p in self.files():
            if "/vendor/" in str(p) or p.name == "test_public.py":
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for m in bad.finditer(line):
                    if not m.group(0).startswith(allowed):
                        hits.append(f"{p.relative_to(ROOT)}:{i}: {m.group(0)[:40]}")
        self.assertEqual(hits[:15], [])
        for manifest in (ROOT / ".claude-plugin/marketplace.json", helpers.PLUGIN / ".claude-plugin/plugin.json"):
            self.assertNotIn("email", manifest.read_text())
        ignore_file = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/internal/", ignore_file.splitlines())
        self.assertIn("/dist/", ignore_file.splitlines())
        for path in ("dist/release.tar.gz", "internal/notes.md"):
            cp = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", path], cwd=ROOT)
            self.assertEqual(cp.returncode, 0, f"{path} should be ignored")
        for path in ("bridge/dist/package.json", "bridge/internal/guide.md"):
            cp = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", path], cwd=ROOT)
            self.assertEqual(cp.returncode, 1, f"{path} should not be ignored by the root-only rules")

    def test_documents_point_at_files_that_exist(self):
        for doc in ("README.md", "README.tr.md", "ORKESTRA.md", "CONTRIBUTING.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md"):
            text = (ROOT / doc).read_text(encoding="utf-8")
            for target in re.findall(r"\]\(((?!https?:|#|mailto:)[^)\s]+)\)", text):
                self.assertTrue((ROOT / target.split("#")[0]).exists(), f"{doc} links to missing {target}")
            for name in re.findall(r"`((?:[\w.-]+/)*[\w.-]+\.(?:md|sh|html))`", text):
                if "/" in name or name.isupper() or name[0].isupper():
                    hit = (ROOT / name).exists() or (helpers.PLUGIN / name).exists() or list(ROOT.glob(f"**/{name}"))
                    self.assertTrue(hit or name.startswith(("<", "~")) or "*" in name, f"{doc} names missing file {name}")


if __name__ == "__main__":
    unittest.main()
