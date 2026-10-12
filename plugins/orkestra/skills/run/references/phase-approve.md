# Phase: approve (the plan integrity gate before code changes)

Read `approval_mode` from `.orkestra/current/state.json`.

- `autopilot` (default): do not ask or wait for the user. Run `orkestra plan check -p <project>` and ensure the current
  spec/plan has a passing critic review. `orkestra phase next` records a hash-bound policy approval only if those
  checks still pass. Never write `approval.json` or call a hook to simulate consent.
- `manual`: use the single user approval gate below. Hooks alone record the user's actual answer; source edits remain
  blocked until that current plan hash is approved.

1. Show a compact summary in chat (Turkish, ≤ 25 lines):
   - Hedef (1–2 cümle) · Kapsam içi / dışı (madde)
   - Görevler: `T001 — …` (one line each)
   - Dokunulacak dosyalar (count + key paths) · Yeni bağımlılık (or "yok") · Risk + geri alma
   - Doğrulama: which gates + key tests
   - Full plan: `<run folder>/plan.md`
2. Ask with **AskUserQuestion** exactly — ONE question in the call, nothing else (extra-scope questions belong to
   clarify; the hook reads only the approval question's answer and hands any other answer back to you as context):
   - header: `Plan onayı`
   - question: `Plan ve spec'i onaylıyor musun?`
   - options: `Onayla — uygulamaya başla` · `Değişiklik istiyorum` · `İptal et`
3. Outcomes:
   - Approved → the hook says "approval recorded" → `orkestra phase next` → continue immediately (no further confirmation needed; that's the deal).
   - Changes → apply them to spec/plan (they are not frozen yet), re-run plan-critic if the change is non-trivial, `orkestra plan check`, ask again.
   - Cancel → `orkestra abort "kullanıcı iptal etti"`.
   - The user may approve with "onay", "onaylıyorum", "onay verdim", or a sentence like "bu planı onaylıyorum yap …" — the hook records clear approve intent (not only the bare word).
4. If `phase next` says no approval was recorded even though the user approved, ask them to type `onay`.
