import unittest

import helpers  # noqa: F401
from orkestra_core.intent import approval, git_grants

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


if __name__ == "__main__":
    unittest.main()
