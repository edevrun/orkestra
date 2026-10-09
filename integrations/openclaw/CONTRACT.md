# OpenClaw → orkestra sözleşmesi (orkestra-core yazar, stabil)

OpenClaw orkestrayı **bridge** üzerinden çağırır. OpenClaw'ın kendi config/skill yapısı openclaw-agent'ın alanıdır;
bu belge yalnız orkestra tarafını tanımlar.

## Önkoşullar (aynı Mac)
- `bash ~/Projects/orkestra/install.sh` (plugin + `~/.orkestra/bridge.secret.json` token)
- Bridge açık: `python3 ~/Projects/orkestra/bridge/orkestra_bridge.py serve` (ya da `... launchd`)
- `python3 ~/Projects/orkestra/bridge/orkestra_bridge.py doctor` → hazır

## Telegram tek-tüketici kuralı
OpenClaw Telegram botunu zaten dinliyorsa **bridge Telegram'ı dinlemez** (`telegram.bot_token` boş kalır).
Kullanıcının mesajları OpenClaw'a gelir; OpenClaw orkestra komutlarını aşağıdaki istemciyle bridge'e iletir.

## Önerilen komut dili (kullanıcıya görünen)
| Kullanıcı yazar | OpenClaw çalıştırır |
|---|---|
| `/yap <proje> <istek>` | `python3 ~/Projects/orkestra/bridge/orkestra_client.py start <proje> "<istek>" --chat-id <chat> --source openclaw` |
| bekleyen bir soruya/onaya cevap (ör. `onay`) | `python3 ~/Projects/orkestra/bridge/orkestra_client.py reply --chat-id <chat> "<metin>"` |
| `/durum` | `python3 ~/Projects/orkestra/bridge/orkestra_client.py status --chat-id <chat>` |
| `/iptal` | `python3 ~/Projects/orkestra/bridge/orkestra_client.py cancel --chat-id <chat>` |
| `/canli` (öneri; `/durum` da içerir) | `python3 ~/Projects/orkestra/bridge/orkestra_client.py runs` → düz metin; `--json` ile yapı |

Alternatif (tek uç nokta): her mesajı olduğu gibi `POST /telegram {"chat_id", "text"}` ile ilet; yanıttaki `message`
alanını kullanıcıya gönder. Bridge `/yap`, `/durum`, `/iptal` ve cevapları kendisi ayırt eder.

## Bildirimler (orkestra → kullanıcı)
İş soru sorduğunda, onay beklediğinde, bittiğinde ya da engellendiğinde bridge bir bildirim üretir:
- `bridge.secret.json → callback_url`: OpenClaw'ın gelen-webhook adresi verilirse bridge şu JSON'u POST eder:
  `{"id","project","status","phase","run_id","chat_id","message"}`. `message` kullanıcıya olduğu gibi gönderilecek metindir.
  `status`: `awaiting_user` (cevap bekliyor) · `done` · `blocked` · `cancelled` · `error`.
- Webhook mümkün değilse OpenClaw periyodik olarak `orkestra_client.py status --chat-id <chat>` sorgular
  ve `updated` değişen işlerin `last_message`'ını iletir.

## HTTP API (127.0.0.1:47615, `Authorization: Bearer <bridge.secret.json → token>`)
- `POST /jobs {project, request, chat_id?, source?}` → 202 job · aynı projede açık iş varsa 400
- `POST /jobs/<id>/reply {text}` · `POST /reply {chat_id, text}` → 202
- `GET /jobs[?chat_id=]` · `GET /jobs/<id>` · `GET /health`
- `GET /runs` → `{"runs": [...]}` (1.2.0+, eklemeli): her açık çalışma için `project_name, run_id, phase, status,
  live{level,text}, tasks_done, tasks_total, activity[{time,agent,kind,tool,what}]`. `live.level`:
  `run|wait|quiet|stale|blocked|paused|done`; `wait` = kullanıcıdan cevap bekliyor.
- `POST /telegram {chat_id, text}` → `{message}` (komut dili; `allowed_chat_ids` doluysa yalnız onlar)

## Garantiler
- Headless run'lar etkileşimli olanla aynı guard'lardan geçer: commit/push/deploy yok, sırlar okunmaz, plan onayı yalnız
  kullanıcının cevabıyla (`onay`) kaydedilir — OpenClaw bunu kullanıcı adına göndermemeli.
- Proje başına tek açık run; ikinci istek 400 döner.
- `allowed_projects` (bridge.secret.json) ile uzaktan başlatılabilecek projeler sınırlanabilir.
