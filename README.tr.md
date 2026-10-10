> English: [README.md](README.md)

# Orkestra — Claude Code ve Cursor için uçtan uca agent orkestrası

Şef sensin. Bir istek yazarsın; Orkestra o projeyi tanır, yalnız sonucu değiştiren soruları sorar,
`plan.md` hazırlar, **tek onayınla** bir agent ekibine TDD ile uygulatır, çalışan hiçbir şeyin
bozulmadığını kanıtlar, bağımsız review'dan geçirir ve proje hafızasını güncelleyerek teslim eder.
Hiçbir şeyi commit/push/deploy etmez — sen istemedikçe.

```
/orkestra:run shop'a sipariş listesine CSV dışa aktarma ekle
```

## Kurulum (bir kez)

```bash
bash ~/Projects/orkestra/install.sh          # etkileşimli
bash ~/Projects/orkestra/install.sh --yes    # varsayılanlar: Context7 + Playwright MCP, bridge token
```

Kurulum önce eklenti ve bridge Python testlerini senin makinende koşar (bu Mac'te ~4 dk; CPU ve sistem yüküne göre değişir).
Biri bile kırmızıysa kurulumu durdurur. İsteğe bağlı OpenClaw Node.js testleri ayrıca `npm test` ile çalıştırılır.
Plugin `~/Projects/orkestra/plugins/orkestra`'dan yerinde yüklenir; buradaki her düzenleme bir sonraki
Claude Code oturumunda geçerli olur.

## Motor: Claude Code ya da Cursor

Aynı akışı iki motor sürebilir; hangisinin süreceğini isteği verirken seçersin. Bir şey yazmazsan Claude.

| Nereden | Claude (varsayılan) | Cursor |
|---|---|---|
| Sohbet | Claude Code: `/orkestra:run <istek>` | Cursor (IDE ya da `agent`): `/orkestra-run <istek>` |
| Terminal | `orkestra run -p <proje> "<istek>"` | `orkestra run --engine cursor -p <proje> "<istek>"` |
| Telegram | `/yap <proje> <istek>` | `/yap cursor <proje> <istek>` |
| Bridge API | `{"project": …, "request": …}` | `… , "engine": "cursor"}` |

Cursor'u bir kez hazırla (Cursor CLI: https://cursor.com/cli):

```bash
python3 ~/Projects/orkestra/plugins/orkestra/bin/orkestra cursor install   # --dry-run ile önce ne yapacağını gösterir
python3 ~/Projects/orkestra/plugins/orkestra/bin/orkestra cursor status
```

Kurulum `~/.cursor/skills` ve `~/.cursor/agents` altına orkestra skill ve alt-ajanlarını bağlar, `~/.cursor/hooks.json`'a
orkestra girdilerini **senin girdilerine dokunmadan** ekler (önce `hooks.json.orkestra-backup-<zaman>` yedeği alır) ve
`orkestra` komutunu `~/.local/bin`'e koyar. Geri almak: `orkestra cursor uninstall`. Yeni açılan Cursor sohbetlerinde geçerlidir.

Cursor'da fark eden üç şey:
- **Soru ve onay sohbette yazıyla olur.** Model planı gösterir ve durur; onaylamak için `onay` yazarsın (Telegram'daki gibi).
- **Yalnız orkestra run'ı süren sohbetler denetlenir.** Diğer Cursor sohbetlerine dokunulmaz. Her-oturum kurallarını
  (commit/push izni, sır dosyaları, store) tüm Cursor sohbetlerine de uygulamak istersen `~/.orkestra/config.json` →
  `"engine": {"cursor": {"global_guards": true}}`.
- **Varsayılanı değiştirmek:** aynı dosyada `"engine": {"default": "cursor"}`. Bir run'ın motoru `orkestra status`,
  `watch` ve panelde görünür; yarım kalan run'ı başlatan motor sürdürür.

Cursor, Claude Code'un eklenti kaydını da okur ve oradaki orkestra kopyasının hook'larını kendi sohbetlerinde çalıştırır.
O kayıt eski bir kopyayı gösteriyorsa (`orkestra cursor status` söyler) eski kurallar Cursor'da da cevap verir; kaydı
`claude plugin update orkestra@orkestra-local` yeniler.

## Komutlar

| Komut | Ne yapar |
|---|---|
| `/orkestra:run <istek>` | Uçtan uca akış. Kökte (`~/Projects`) açtıysan proje adını istekte geçir. |
| `/orkestra:status` | Faz, görevler, kanıt eksikleri, seni bekleyen bir şey var mı |
| `/orkestra:resume` | Yarım kalan run'a (yeni oturum, compaction, ara verme) kaldığı yerden devam |
| `/orkestra:onboard` | Projenin doğrulanmış hafızasını (PROJECT.md + gate'ler) kurar/yeniler |
| `/orkestra:rollback` | Run'ın değiştirdiği dosyaları run öncesine döndürür. Önce kuru çalıştırma gösterir, onayını ister. Run sırasında senin yaptığın değişikliklere dokunmaz (`--all` demedikçe). Geri almadan önce mevcut içeriği yedekler. git'e yazmaz |
| `/orkestra:ship` | Commit + push. Yalnız sen çağırabilirsin |

## Canlı takip: hangi pipeline nerede, şu an ne yapıyor

| Nereden | Komut / adres |
|---|---|
| Terminal (canlı, 2 sn'de bir yenilenir) | `python3 ~/Projects/orkestra/plugins/orkestra/bin/orkestra watch` · tek seferlik: `--once` · bitenler de: `--all` · tek proje: `-p <proje>` |
| Tarayıcı (salt-okunur, yalnız bu Mac) | `python3 ~/Projects/orkestra/plugins/orkestra/bin/orkestra dashboard --open` → http://127.0.0.1:47616/ |
| Yerel chat ve canlı flow haritası | Bridge çalışırken http://127.0.0.1:47615/ · fazları, kayıtlı ajanları, işleri ve son hook hareketlerini izle |
| Telegram | `/durum` → bu sohbetin işleri + tüm açık çalışmaların canlı durumu |
| OpenClaw / script | `python3 ~/Projects/orkestra/bridge/orkestra_client.py runs` (ya da `GET /runs`) |

Her çalışma için: faz çubuğu, bu fazda geçen süre, **şu anki durum** (çalışıyor · komut çalışıyor · sessiz ·
SENİ BEKLİYOR · bloke · oturum kapanmış olabilir), görev ilerlemesi ve R/G kanıtı, kapı sonuçları, bağlı Claude
oturumu ve son araç çağrıları (hangi ajan hangi dosyayı okudu/yazdı, hangi komutu koştu, hangi guard neyi engelledi).
Yerel chat'in **akış haritası**, bu gerçek run verisini faz çizgisi, raporlanan alt-agent ağı, etkin görevler ve son altı hook olayı olarak gösterir; yaklaşık 6 saniyede yenilenir.
Kaynak dosyalar: `<proje>/.orkestra/runs/<run>/activity.jsonl` (hook'lar yazar, git'e girmez) ve
`<proje>/.orkestra/current/ledger.md` (faz geçişleri). `watch`/`dashboard` hiçbir şeyi değiştirmez.
Kısa yol istersen `~/.zshrc`'ye: `alias orkestra="python3 ~/Projects/orkestra/plugins/orkestra/bin/orkestra"`.

### Tempo ve kesintisiz devam (1.4.0)

| Komut | Ne yapar |
|---|---|
| `orkestra next -p <proje>` | Sıradaki **tek** adım (en çok 3 satır). State, kanıt ve inceleme dosyalarından hesaplanır; aynı durumda hep aynı cevabı verir. Oturum kapansa, bağlam sıkışsa ya da yeni oturum açsan da kaldığın alt-adımı bulur: kanıtı olan görev yeniden yazılmaz, dosyadaki karar uygulanır, soru ve onay asla senin yerine cevaplanmaz. `/orkestra:resume` bunu kullanır. |
| `orkestra tempo -p <proje> [--run <id>]` | Süre nereye gitti: faz süreleri, görev başına süre, inceleme retleri, ölçülen ölü bekleme payı. Biten run'larda da çalışır. |
| `orkestra agents [clear] -p <proje>` | Arka planda çalışan alt-ajan kayıtları (çalışan / iz bırakmayan). `clear`: gerçekten gitmiş ajanların kaydını siler; normalde gerekmez (oturum yeniden başlayınca, rapor gelince ve `resume` sırasında kendiliğinden düşer). |
| `orkestra watchdog [--dry-run\|--json\|--quiet\|--launchd]` | Oturumun dışından tek tarama: seni mi bekliyor, ölü bekleme mi, asılı komut mu, sessiz oturum mu. Bir kez bildirir (macOS bildirimi; bridge açıksa Telegram). Yalnız izin verdiysen devam ettirir. |

Durma sınıfları (`watch`, panel ve `watchdog` aynı sınıflandırıcıyı kullanır): **1** seni bekliyor (meşru) ·
**2** ölü bekleme (alt-ajan bitti, ana oturum 90 sn'dir hareketsiz) · **3** asılı komut (15 dk; kapılarda 30 dk) ·
**4** sessiz oturum (10 dk kayıt yok) · **5** döngü (aynı görevde 3. inceleme turu, aynı guard reddi ya da aynı komut 3 kez).
Eşikler ve otomatik devam: `~/.orkestra/config.json` → `watchdog` (`auto_resume`: `off` · `headless` (varsayılan) · `all`,
`max_auto_resumes`: 3). Varsayılan davranış: Telegram/bridge ile başlatılan başsız iş durursa aynı oturumda en çok 3 kez
kendiliğinden devam eder; etkileşimli sohbet yalnız bildirilir (dışarıdan açık bir sohbete yazılamaz).
Bridge çalışıyorsa taramayı dakikada bir kendisi yapar; bridge kullanmıyorsan `orkestra watchdog --launchd`.

İstek yazmak için slash komutu şart değil: "shop'a X ekle" gibi bir geliştirme isteği `run` skill'ini
kendiliğinden tetikler. Soru sorduğunda ya da açıklama istediğinde tetiklenmez.

## Akış

```
intake → discover → clarify → plan → approve → implement → verify → review → deliver → done
          │           │         │        │          │          │        │         │
       scout'lar   sorular  architect  SENİN    implementer  gate     reviewer  rapor +
       + baseline  (≤4/tur) + critic   ONAYIN   (TDD) +      verify   + adversary hafıza
       gate'ler                                  reviewer    verifier  + triage
```

Her faz geçişi `orkestra phase next` ile yapılır ve **deterministik ön koşullara** bağlıdır.
Örneğin context.md ve baseline yoksa clarify'a, plan-critic `PASS` vermeden onaya, onayın yoksa koda,
gate'lerde regresyon varsa review'a geçilmez. Model bu kapıları atlayamaz. Kapıları kod uygular,
prompt değil.

### Agent ekibi
| agent | model | rol |
|---|---|---|
| scout | haiku | salt-okur kod haritası (paralel) |
| researcher | sonnet | sürüme özgü güncel doküman (Context7/web) |
| architect | opus | spec.md + plan.md |
| plan-critic | opus | planı sana gelmeden önce çürütmeye çalışır |
| implementer | sonnet | tek görev, sıkı TDD (red → green kanıtı CLI'ye kaydedilir) |
| reviewer | opus | spec uyumu + doğruluk + regresyon riski |
| adversary | opus | kenar durum, güvenlik, hata modları |
| verifier | sonnet | gereksinimden geriye doğrulama, verification.md |

Model değiştirmek için ilgili `plugins/orkestra/agents/<ad>.md` dosyasındaki `model:` satırını düzenle.

## Garantiler: hook'larla zorlananlar

Bunlar prompt değil, `PreToolUse`/`UserPromptSubmit`/`Stop` hook'ları:

- **Git:** O anki mesajında açıkça istemedikçe ("commitle", "pushla", "branch aç", "PR aç") branch,
  add, commit, push, merge, reset ve stash çalışmaz. `--no-verify`, main'e force-push ve git config/remote
  değişikliği hiç çalışmaz. İzin yalnız o mesaj için geçerlidir.
- **Sırlar:** `.env*`, `*.env`, `*.p8`, `*.pem`, private key'ler, service-account JSON'ları (içerik taraması dahil),
  `~/.aws/credentials`, `key.properties`, token içeren `.npmrc`, `*.mobileprovision` okunamaz, yazılamaz, taşınamaz
  ve silinemez. Bu kural Read, Grep, Edit/Write ve Bash yönlendirmelerinin hepsi için geçerli.
- **Orkestra'nın kendi ayarları:** `~/.orkestra` (guard ayarları, izinler, bağlamalar) modelin yazabileceği bir yer değil.
- **Mağaza:** `eas build` (`--local` olmadan), `eas submit`, `eas update` ve `expo publish` engelli
  (kural 06).
- **Run sırasında:**
  - Plan onaylanmadan kaynak koda yazılamaz.
  - Onaydan sonra `spec.md` donar.
  - Başka projeye yazılamaz.
  - Planda onaylanmamış bağımlılık kurulamaz; `package.json`'a elle eklenen paket de verify'da yakalanır.
  - Deploy, publish, prod migration, ssh ve uzak rsync çalışmaz.
  - Salt-okur agent'lar yalnız rapor dosyası yazabilir.
  - MCP düzenleme araçları da aynı kurallara tabi (ör. Serena'nın `replace_symbol_body` aracı).
- **Onay:** Plan onayını model kendisi veremez. Onayı yalnız hook'lar kaydeder: senin "Onayla" seçimin ya
  da "onay" yazman. Onaydan sonra plan değişirse yeniden onay gerekir.
- **Regresyon:** Baseline'da geçen her gate sonunda da geçmek zorunda (`orkestra gate verify`).
  Baseline'da zaten kırık olan gate'ler run'a yazılmaz, raporda ayrıca belirtilir.
- **Kanıt katmanı (halüsinasyona karşı):**
  - Plandaki her dosya gerçekten var olmak zorunda; yeni dosyalar `(yeni)` diye işaretlenir.
  - Kırmızı ve yeşil test aynı komutla koşulur. Kırmızıdan sonra test değiştirilirse ya da test dosyası hiç
    değişmezse görev kapanmaz.
  - Silinen test, eklenen `skip/only/xfail` ya da `@ts-ignore`/`eslint-disable`/`# type: ignore` taranır.
    İstisna yalnız senin onayladığın planda yazıyorsa geçerli.
  - `verification.md` var olmayan bir dosyayı ya da test adını gösteremez. "Elle denedim" iddiası,
    `orkestra evidence` ile kaydedilmiş bir log olmadan kabul edilmez.
  - Raporun kanıt bölümünü CLI makine verisinden üretir. Model bu bölümü değiştirirse kapanış reddedilir.
- **Yarıda bırakmama:** Run aktifken model turu bitirmeye kalkarsa `Stop` hook'u onu devam ettirir
  (prompt başına en fazla 2 kez) ve sıradaki adımı (`orkestra next`) mesaja yazar. Seni bekliyorsa, gerçekten
  engellendiyse ya da **arka planda bir alt-ajan çalışıyorsa** durabilir: alt-ajanın raporu yeni mesaj olarak gelir ve
  turu kendisi başlatır.
- **Boşa bekleme yok:** Ana oturum alt-ajan sonucunu `sleep`/döngüyle bekleyemez (`wait` guard'ı). 4 Ekim'de duvar
  saatinin %12–34'ü bu bekleyişe gitmişti (`orkestra tempo` ölçer).
- **Ekranına pencere açma yok:** Run sırasında macOS sistem penceresi açan komutlar (`security show-keychain-info`,
  parolasız `unlock-keychain`, `osascript` diyalogları, `xcode-select --install`) çalışmaz (`dialogs` guard'ı).
- **Alt-ajan raporu kullanıcı değildir:** `<agent-message …>` / `<task-notification>` ile gelen metin git izni vermez,
  planı onaylamaz ya da reddetmez, "kullanıcı cevap verdi" sayılmaz.
- **İnceleme turları:** `PASS` alan görev küçük bulgular (minor) için yeniden açılmaz; bulgular final incelemeye taşınır.
  Aynı görevde üçüncü tur senin kararını ister (`--force "<gerekçe>"` ledger'a yazılır).
- **Aynı anda iki görev:** Dosya listeleri çakışan iki görev birlikte başlatılamaz.

Guard'ları `~/.orkestra/config.json` → `guards` altından tek tek kapatabilirsin.

## Hafıza: yeni özellikleri hatırlama

Her projede `.orkestra/` klasörü:

```
.orkestra/
  config.json            gate komutları (test/lint/typecheck/build), onaylı mı
  memory/PROJECT.md      stack, yapı, konvansiyonlar, komutlar (her run ilk 25 satırı okur)
  memory/FEATURES.md     teslim edilen her run'ın tek satırı
  memory/specs/*.md      yetenek başına güncel davranış (living spec)
  memory/DECISIONS.md    mimari kararlar ·  memory/LEARNINGS.md  tekrarlanmaması gereken dersler
  features/NNN-slug/     request, context, spec, plan, ledger, görev raporları, review'lar, verification, report
  runs/                  makine durumu, yedekler, kanıt logları (git-ignore'lu)
```

Token stratejisi: Hafıza kademeli açılır. Önce özet okunur, detaya yalnız gerektiğinde inilir.
Subagent'lara planın tamamı değil yalnız görev brief'i verilir. Agent'lar ayrıntıyı dosyaya yazar ve
6–8 satırla döner. Mekanik işi haiku/sonnet yapar, opus yalnız tasarım ve muhakemede çalışır.
Plugin'in her oturuma eklediği sabit yük yaklaşık 1,3k token; hook'ların bağlama maliyeti yoktur.

## Telegram, OpenClaw ve n8n

**Telegram (tek komutla kurulum):**
```bash
python3 ~/Projects/orkestra/bridge/orkestra_bridge.py telegram-setup   # BotFather token'ı ister, chat id'ni yakalar
python3 ~/Projects/orkestra/bridge/orkestra_bridge.py launchd          # bridge her açılışta çalışsın (ya da: serve)
python3 ~/Projects/orkestra/bridge/orkestra_bridge.py doctor --live    # her şey hazır mı
```
Sonra Telegram'da: `/yap shop sipariş listesine CSV dışa aktarma ekle` → sorular ve plan özeti gelir →
düz yazıyla cevap verirsin, onay için `onay` → bitince rapor gelir. `/durum` (işler + canlı durum), `/iptal`.
Bridge Telegram'ı long-polling ile dinler, public URL gerekmez. Yalnız kurulumda yakalanan chat ve kullanıcı yazabilir.

**OpenClaw:** OpenClaw zaten bir Telegram botunu dinliyorsa aynı botu bridge'e verme; bir bot token'ını yalnız tek
program dinleyebilir. Ya orkestraya ayrı bir bot ver, ya da OpenClaw'dan `bridge/orkestra_client.py` ile çağır
(`integrations/openclaw/CONTRACT.md`).

**n8n:** `bridge/n8n/` altında iki workflow var, `install.sh` yerel n8n'e aktarabilir.
- `POST /webhook/orkestra-job` her türlü tetikleyiciden (GitHub, Gmail, iOS Kısayollar, zamanlayıcı) iş başlatır.
  `X-Orkestra-Key` header'ı zorunlu.
- `orkestra-callback` bildirimleri istediğin kanala dağıtır.

Headless run'lar da aynı guard'larla çalışır. Plan onayı yalnız senin `onay` cevabınla kaydedilir.
Mac uykudaysa bridge çalışmaz; Telegram'dan iş başlatmak için Mac açık ve internete bağlı olmalı.

## Dürüst sınırlar

- **%100 hatasızlık vaat eden yok, burada da yok.** Burada yapılan şu: modelin hatası kod tabanına
  sızmadan önce bir gate, bir hook ya da bir reviewer onu yakalasın.
- **Kanıt gate'ler kadar güçlü.** Projede test yoksa regresyon ağı da yok. Orkestra bunu raporda risk
  olarak yazar ve ilk run'da test eklemeyi önerir.
- **Shell guard'ları sezgisel çalışır.** Yaygın yazım biçimlerini kapsar. Kasıtlı olarak gizlenmiş bir
  komutu (string birleştirme gibi) yakalamayabilir. Model düşman değil ama dikkatsiz olabilir; guard'lar
  dikkatsizliğe karşı tasarlandı.
- **Bilinmeyen git alias'ları** (`git ci` gibi) tanınmaz.
- **AskUserQuestion onayı kanıt olarak tool çıktısından okunur.** Format değişirse "onay" yazman yeter.
- **Guard'lar bir kum havuzu değil.** Gerçek bir denemede bir model, onayı beklemek yerine hook betiğini kendisi
  çalıştırıp planını onayladı (1.5.0'da kapatıldı: böyle komutlar ve dosyalar reddedilir, hook kendisini ajanın
  başlattığını tanır). Ajan senin kullanıcınla kod çalıştırabildiği sürece kararlı ve gizlenen bir betik her dosya
  tabanlı denetimi aşabilir; kayıtlar (`ledger.md`, `activity.jsonl`, `hooks.log`) bunu sonradan görmeni sağlar.
- **Cursor:** komut satırı aracıyla (`agent`) uçtan uca denendi; IDE penceresi aynı hook sözleşmesini kullanır ama
  ayrıca denenmedi. Cursor run'larında token sayımı ve implementer için "yeşil kanıt olmadan bitirme" uyarısı yok
  (Cursor alt-ajan bitişinde olay göndermiyor; `task done` kapısı aynen geçerli).

## Bakım

```bash
python3 plugins/orkestra/bin/orkestra doctor                  # ortam + hook öz-testi + MCP durumu
cd plugins/orkestra/tests && python3 -m unittest discover -s . -q # 357 test
cd bridge/tests && python3 -m unittest discover -s . -q           # 19 bridge testi
cd integrations/openclaw/plugin && npm test                       # 15 test
bash install.sh --refresh-vendor                               # referans projeleri upstream'den tazele
```

`plugins/orkestra/vendor/` altında superpowers, spec-kit, BMAD, GSD-core, OpenSpec, humanlayer,
compound, ECC ve karpathy-skills projelerinden seçilmiş, lisanslarıyla sabitlenmiş referans dosyalar var
(`PINNED.md`). Bunlar otomatik yüklenmez; playbook'lar bunlardan damıtıldı.

İsteğe bağlı kontrol listeleri (ECC'den damıtıldı; yalnız diff eşleşince ilgili ajan yükler, sabit maliyeti yok):
`orkestra playbook security | stack-web | stack-mobile | data | ui-flow`.

Katkı kuralı: açık bir run varken hook/guard/agent dosyası değiştirilmez (`CONTRIBUTING.md`). Tek sayfalık özet: `ORKESTRA.md`;
şemalı anlatım: `docs/orkestra-mimari.html`.
