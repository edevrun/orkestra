import unittest

import helpers  # noqa: F401
from orkestra_core.intent import (approval, git_grants, infer_size, manual_approval_requested,
                                  min_context_chars, off_topic_vs_project, skips_adversary, skips_plan_critic)

GRANT_CASES = {
    "commitle ve pushla": ["commit", "push"],
    "değişiklikleri commitleyip pushlar mısın": ["commit", "push"],
    "son commit'te ne değişti?": [],
    "commit mesajı öner": [],
    "commitleme sakın, ben yapacağım": [],
    "commitlemeden önce testleri koş": [],
    "please commit and push": ["commit", "push"],
    "don't push yet, just commit": ["commit"],
    "Commit at ama push etme": ["commit"],
    "git push yap": ["push"],
    "bu dosyayı düzelt": [],
    "pushlamadan önce bana göster": [],
    "commit this": ["commit"],
    "the commit hash is abc": [],
    "/orkestra:ship": ["commit", "push"],
    "/orkestra:rollback": ["discard"],
    "yeni branch aç ve commitle": ["branch", "commit"],
    "branch açma": [],
    "PR aç": ["pr"],
    "force push yap": ["force", "push"],
    "commit history'yi incele": [],
    "Tüm değişiklikleri COMMİTLE": ["commit"],
    "commit sayısı kaç": [],
    "kodu yaz, sonra commit et": ["commit"],
    "commitlenmesin": [],
    "push the changes to origin": ["push"],
    "no commit please": [],
    "issue aç": ["gh_write"],
    "main'i merge et": ["sync"],
    "git pull yap": ["sync"],
    "v1.2 tag oluştur ve pushla": ["push", "tag"],
    "pull request'e bak": [],
    "shop'a dark mode ekle": [],
    "değişiklikleri geri al": ["discard"],
    "push bildirimi ekle": [],
    "push notification ekle": [],
    "Push servisini düzelt": [],
    "pushlandı mı?": [],
    "commitledin mi?": [],
    "commitler neler?": [],
    "commitleri listele": [],
    "main'e geç": ["branch"],
    "switch to main": ["branch"],
    "git stash yap": ["stash"],
    "amend the last commit": ["amend", "commit"],
    "v1.2 tagla": ["tag"],
    "son değişiklikleri çek": ["sync"],
    "pushla ama force push yapma": ["push"],
    "pushlar mısın?": ["push"],
    "reset --hard yap": ["discard", "hard_reset"],
}

APPROVAL_CASES = {
    "onaylıyorum": "approve",
    "Onay": "approve",
    "evet": "approve",
    "devam et": "approve",
    "LGTM": "approve",
    "onaylıyorum ama testleri ekle": "reject",
    "hayır": "reject",
    "T003'ü değiştir": "reject",
    "şu dosyayı da incele ve sonra plana bak lütfen bunu yapalım mı acaba": None,
    "": None,
    "onay vermiyorum": "reject",
    "Ok bekle": None,
    "Tamam yarın bakarım": None,
    "tamam mı sence bu plan?": None,
    "devam etmeden önce bir soru sorayım": None,
    "go back to the plan": None,
    "Onaylıyorum.": "approve",
    "evet başla": "approve",
    # Sentence-level approval (Cursor: user rarely types bare "onay")
    "onay verdim": "approve",
    "onay veriyorum": "approve",
    "onaylıyorum yap": "approve",
    "planı onaylıyorum": "approve",
    "bu planı da onaylıyorum yap en uzman şekilde": "approve",
    "bu planı da onaylıyorum yap en uzman şekilde en kapsamlı testlerle hiçbir sağlıklı yapıyı bozmadan yap": "approve",
    "onay veriyorum devam et": "approve",
    "I approve — ship it": "approve",
    "yap": None,
    "onaylıyor musun?": None,
    "onaylıyorum ama T003'ü çıkar": "reject",
}


class TestIntent(unittest.TestCase):
    def test_manual_approval_mode_requires_an_explicit_positive_request(self):
        cases = {
            "ben karar verici olmalıyım": True,
            "kararları ben vermeliyim": True,
            "approve aşamasında onayı bana sor": True,
            "I want to decide each approval": True,
            "let me approve the plan": True,
            "Default to autopilot unless I say 'let me decide': ask me then": False,
            "Varsayılan autopilot; kullanıcı isterse ben karar verici olmalıyım derse onay sor": False,
            "Don't ask me, just do it autonomously": False,
            "kararı sen ver, onay bekleme": False,
            "onay nedir?": False,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(manual_approval_requested(text), expected)

    def test_approval_answers(self):
        from orkestra_core.intent import approval_answer
        self.assertEqual(approval_answer("Onayla — uygulamaya başla"), "approve")
        self.assertEqual(approval_answer("Onayla ama T3'ü çıkar"), "reject")
        self.assertEqual(approval_answer("Değişiklik istiyorum"), "reject")
        self.assertEqual(approval_answer("İptal et"), "reject")

    def test_git_grants(self):
        for text, expected in GRANT_CASES.items():
            with self.subTest(text=text):
                self.assertEqual(git_grants(text), sorted(expected))

    def test_approval(self):
        for text, expected in APPROVAL_CASES.items():
            with self.subTest(text=text):
                self.assertEqual(approval(text), expected)



class TestOffTopic(unittest.TestCase):
    def _ws(self):
        from pathlib import Path
        import tempfile, os
        root = Path(tempfile.mkdtemp(prefix="ot-"))
        bound = root / "sample-app"
        other = root / "orkestra"
        bound.mkdir(); other.mkdir()
        os.environ["ORKESTRA_WORKSPACE"] = str(root)
        return root, bound, other

    def test_other_workspace_project_is_off_topic(self):
        import os
        root, bound, other = self._ws()
        try:
            self.assertTrue(off_topic_vs_project(
                "bu chatte orkestra projesi ile ilgili geliştirme yap", bound, "003-x"))
            self.assertFalse(off_topic_vs_project(
                "sample-app T041 devam", bound, "003-x"))
            self.assertFalse(off_topic_vs_project("devam et", bound, "003-x"))
            self.assertFalse(off_topic_vs_project(
                "Approve aşamasında onayı bana sor; ben karar verici olayım.", bound, "003-x"))
        finally:
            os.environ.pop("ORKESTRA_WORKSPACE", None)

    def test_unnamed_request_without_continue_is_off_topic(self):
        """Gap: 'make this dashboard friendlier' while bound must unbind (no project name)."""
        import os
        root, bound, _other = self._ws()
        try:
            self.assertTrue(off_topic_vs_project(
                "make this dashboard friendlier", bound, "003-demo-kayit",
                title="Demo Kayıt"))
            self.assertTrue(off_topic_vs_project(
                "bu paneli daha okunaklı yap", bound, "003-x"))
            self.assertFalse(off_topic_vs_project("continue from here", bound, "003-x"))
            self.assertFalse(off_topic_vs_project("/orkestra-resume sample-app", bound, "003-x"))
            self.assertFalse(off_topic_vs_project("onaylıyorum", bound, "003-x"))
            self.assertFalse(off_topic_vs_project(
                "demo kaydını düzelt", bound, "003-x", title="Demo Kayıt"))
            self.assertFalse(off_topic_vs_project("şimdi commit et", bound, "003-x"))
            self.assertFalse(off_topic_vs_project("testleri de çalıştır", bound, "003-x"))
        finally:
            os.environ.pop("ORKESTRA_WORKSPACE", None)


SIZE_CASES = {
    "typo düzelt README": "XS",
    "çok basit rename: Foo → Bar": "XS",
    "boyut: XS sadece bunu değiştir": "XS",
    "sub fonksiyonu ekle": "S",
    "küçük bugfix: null check": "S",
    "dark mode özelliği ekle": "M",
    "yeni ekran ve endpoint ekle": "M",
    "add oauth login": "M",
    "tüm uygulamayı migrate et": "L",
    "bulk multi-platform refactor": "L",
    "size: L rewrite the whole module": "L",
}


class TestInferSize(unittest.TestCase):
    def test_cases(self):
        for text, want in SIZE_CASES.items():
            self.assertEqual(infer_size(text), want, text)

    def test_empty_defaults_m(self):
        self.assertEqual(infer_size(""), "M")

    def test_slim_helpers(self):
        self.assertTrue(skips_plan_critic("XS") and skips_plan_critic("S"))
        self.assertFalse(skips_plan_critic("M"))
        self.assertTrue(skips_adversary("S") and not skips_adversary("L"))
        self.assertEqual(min_context_chars("XS"), 80)
        self.assertEqual(min_context_chars("S"), 200)



if __name__ == "__main__":
    unittest.main()
