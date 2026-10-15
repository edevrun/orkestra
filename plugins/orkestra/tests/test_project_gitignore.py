"""A project that already has a .gitignore keeps .orkestra/ out of the repository."""
from __future__ import annotations

import subprocess
from pathlib import Path

import helpers
from orkestra_core.runs import ensure_project_gitignore, init_project


class TestProjectGitignore(helpers.Sandbox):
    def test_appends_the_directory_line_once(self) -> None:
        project = self.make_project("demo")
        gi = project / ".gitignore"
        gi.write_text("dist/\n", encoding="utf-8")
        before = gi.read_bytes()
        self.assertTrue(ensure_project_gitignore(project))
        text = gi.read_text(encoding="utf-8")
        self.assertIn("\n.orkestra/\n", text)
        self.assertNotIn("*.orkestra", text)
        self.assertFalse(ensure_project_gitignore(project))
        self.assertTrue(gi.read_text(encoding="utf-8").count(".orkestra/") == 1)
        self.assertNotEqual(gi.read_bytes(), before)
        if shutil_git():
            cp = subprocess.run(["git", "check-ignore", "-v", "--", ".orkestra/runs/state.json"],
                                cwd=project, capture_output=True, text=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn(".orkestra/", cp.stdout)

    def test_does_not_create_a_gitignore(self) -> None:
        project = self.make_project("demo")
        self.assertFalse((project / ".gitignore").exists())
        self.assertFalse(ensure_project_gitignore(project))
        self.assertFalse((project / ".gitignore").exists())

    def test_existing_patterns_are_left_alone(self) -> None:
        project = self.make_project("demo")
        for line in (".orkestra/\n", ".orkestra\n", "*.orkestra\n", "# note\n**/.orkestra/**\n"):
            gi = project / ".gitignore"
            gi.write_text(line, encoding="utf-8")
            raw = gi.read_bytes()
            self.assertFalse(ensure_project_gitignore(project), line)
            self.assertEqual(gi.read_bytes(), raw)

    def test_init_updates_the_root_file_and_keeps_the_inner_one(self) -> None:
        project = self.make_project("demo")
        (project / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
        init_project(project)
        self.assertIn(".orkestra/\n", (project / ".gitignore").read_text(encoding="utf-8"))
        inner = (project / ".orkestra" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("runs/\n", inner)
        self.assertNotIn(".orkestra/", inner)


def shutil_git() -> bool:
    return Path("/usr/bin/git").exists() or subprocess.run(
        ["git", "--version"], capture_output=True).returncode == 0
