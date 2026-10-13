"""A `#` comment must never hide a later command from the guards (it used to swallow the rest of the string)."""
import copy
import unittest

import helpers  # noqa: F401
from orkestra_core import guards, shellparse as sp
from orkestra_core.paths import DEFAULT_GLOBAL_CONFIG

CFG = copy.deepcopy(DEFAULT_GLOBAL_CONFIG)


def denied(cmd):
    ev = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "/tmp"}
    return guards.pre_tool_decision(ev, CFG, None, []).denied


def programs(cmd):
    return [s.program for s in sp.segments(cmd)]


class TestCommentsDoNotHideCommands(unittest.TestCase):
    def test_dangerous_command_after_a_comment_is_still_denied(self):
        for cmd in ("# note\ngit push origin main", "ls # list\ngit push origin main",
                    "echo a#b; git push origin main", "echo hi # c\n# d\ngit push origin main",
                    "git push origin main # why not", "echo x # c; git push origin main\ngit push origin main"):
            self.assertTrue(denied(cmd), cmd)

    def test_the_hostile_command_alone_is_denied_as_before(self):
        self.assertTrue(denied("git push origin main"))

    def test_segments_see_the_command_after_a_comment(self):
        self.assertEqual(programs("# note\ngit status"), ["git"])
        self.assertEqual(programs("ls # list\ngit status"), ["ls", "git"])
        self.assertEqual(programs("echo a#b; git status"), ["echo", "git"])

    def test_a_comment_is_still_not_a_command(self):
        self.assertEqual(programs("ls # git push origin main"), ["ls"])
        self.assertFalse(denied("ls # git push origin main"))
        self.assertFalse(denied("# git push origin main\nls"))

    def test_hash_inside_quotes_or_a_word_is_data(self):
        self.assertEqual(programs("echo '# x'; git status"), ["echo", "git"])
        self.assertEqual(programs('echo "a # b"\ngit status'), ["echo", "git"])


if __name__ == "__main__":
    unittest.main()
