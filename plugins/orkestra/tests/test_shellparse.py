import unittest

import helpers  # noqa: F401
from orkestra_core import shellparse as sp


def progs(cmd):
    return [s.program for s in sp.segments(cmd)]


class TestSegments(unittest.TestCase):
    def test_chains(self):
        self.assertEqual(progs("npm test && git push origin main; echo ok | tee out.txt"),
                         ["npm", "git", "echo", "tee"])

    def test_newlines(self):
        self.assertEqual(progs("cd app\ngit commit -m x\nls"), ["cd", "git", "ls"])

    def test_quoted_text_is_not_a_command(self):
        self.assertEqual(progs('echo "git push --force && rm -rf /"'), ["echo"])

    def test_bash_c_recursion(self):
        self.assertIn("git", progs("bash -c 'git push origin main'"))
        self.assertIn("git", progs('sh -lc "cd x && git commit -am y"'))

    def test_substitution_recursion(self):
        self.assertIn("git", progs('echo $(git commit -m x)'))
        self.assertIn("git", progs('echo `git push`'))

    def test_heredoc_body_ignored(self):
        cmd = "cat > notes.md <<'EOF'\ngit push --force\nrm -rf /\nEOF\necho done"
        self.assertEqual(progs(cmd), ["cat", "echo"])

    def test_here_string_not_heredoc(self):
        self.assertEqual(progs('grep x <<<"hello"\ngit status'), ["grep", "git"])

    def test_wrappers(self):
        self.assertEqual(progs("sudo -u root env FOO=1 git push"), ["git"])
        self.assertEqual(progs("FOO=bar time nice -n 5 timeout 30 git commit -m x"), ["git"])
        self.assertEqual(progs("npx -y eas-cli build --platform ios"), ["eas-cli"])
        self.assertEqual(progs("uv run --with x pytest -q"), ["pytest"])
        self.assertEqual(progs("pnpm dlx prisma migrate deploy"), ["prisma"])
        self.assertEqual(progs("xargs -I {} git add {}"), ["git"])

    def test_redirects(self):
        seg = sp.segments("echo hi > src/a.ts 2>&1")[0]
        self.assertEqual(seg.redirects, [(">", "src/a.ts")])

    def test_git_call_globals(self):
        seg = sp.segments("git -C /tmp/x -c core.pager=cat --no-pager push -f")[0]
        call = sp.git_call(seg)
        self.assertEqual(call.sub, "push")
        self.assertEqual(call.args, ["-f"])

    def test_unbalanced_quotes_degrade(self):
        self.assertIn("git", progs('echo "oops ; git push'))

    def test_eval(self):
        self.assertIn("git", progs('eval "git push"'))


if __name__ == "__main__":
    unittest.main()
