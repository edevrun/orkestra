# Orkestra — özet (sürüm 1.6.0, 6 Ekim 2026)

Bu dosya Orkestra'nın tamamını tek yerde özetler. Kullanım ayrıntısı `README.md`'de, sürüm geçmişi `CHANGELOG.md`'de,
görsel anlatım `docs/orkestra-mimari.html`'de, katkı kuralları `CONTRIBUTING.md`'dedir.

## Ne olduğu

Orkestra bir Claude Code eklentisidir. Bir geliştirme isteğini on fazdan geçirir: giriş, keşif, netleştirme, plan,
**senin onayın**, uygulama, doğrulama, inceleme, teslim, bitti. Kurallar modelin iyi niyetine bırakılmaz: faz sırasını
ve kanıtı bir komut satırı aracı (`orkestra`) tutar, yasakları Claude Code hook'ları her araç çağrısında denetler.

Planı sen onaylamadan kaynak koda yazılamaz. Her görev önce başarısız, sonra geçen test kanıtıyla kapanır. Run başında
geçen her kontrol (test, lint, tip, build) sonda da geçmek zorundadır. Commit, push, deploy ve mağaza yüklemesi akışın
parçası değildir; yalnız o mesajda açıkça istersen yapılır.

## Mimari

Dört parça var; hepsi `plugins/orkestra/` altında ve yerinde yüklenir (kopyalanmaz):

| Parça | Yer | İş |
|---|---|---|
| Skill'ler ve playbook'lar | `skills/` | Ana oturuma (orkestratör) her fazda ne yapacağını söyleyen metinler. Faz başına bir playbook; isteğe bağlı kontrol listeleri yalnız değişen dosyalar eşleşince yüklenir. |
| Ajanlar | `agents/` | Sekiz alt-ajan: scout, researcher, architect, plan-critic, implementer, reviewer, adversary, verifier. Yalnız implementer kaynak koda yazabilir. |
| CLI | `bin/orkestra` → `lib/orkestra_core/` | Durum makinesi (`runs.py`, `state.py`), kapılar (`gates.py`), TDD ve kanıt (`evidence.py`), geri alma yedekleri (`snapshot.py`), sıradaki adım ve süre (`tempo.py`), izleme (`monitor.py`, `usage.py`), dış tarama (`watchdog.py`). |
| Hook'lar | `hooks/hooks.json` → `hooks/dispatch.py` | Altı olayın tek giriş noktası: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop. Guard kararları `guards.py`'de; kabuk komutunu `shellparse.py`, `orkestra …` satırını CLI'nin kendi ayrıştırıcısı (`cliparse.py`) okur. Hook içindeki bir hata oturumu kilitlemez (izin verir, `~/.orkestra/logs/hooks.log`'a yazar); 8 saniyede bitmeyen denetim ise komutu reddeder. |

`bridge/` ayrı bir süreçtir: Telegram, n8n ya da OpenClaw'dan gelen isteği başsız bir oturuma (Claude Code ya da
Cursor) çevirir. Aynı hook'lar orada da çalışır.

### İki motor (1.5.0)

Bir run'ı Claude Code (varsayılan) ya da Cursor sürer; seçim istek başınadır (`orkestra run --engine cursor`,
`/yap cursor <proje> <istek>`, bridge'de `"engine": "cursor"`, Cursor sohbetinde `/orkestra-run`). Durum makinesi,
kapılar, kanıt ve guard'lar tektir. Motora özgü olan her şey üç dosyadadır:

| Dosya | İş |
|---|---|
| `lib/orkestra_core/engines.py` | Motor adları, oturum anahtarı (`cursor-<sohbet>`), komut satırları, ilk istem, metin çevirisi (`orkestra:reviewer` → `orkestra-reviewer`, yalnız Claude'da doğru cümlelerin Cursor karşılığı). |
| `hooks/cursor_hook.py` | Cursor olaylarını `dispatch.py`'nin işleyicilerine çevirir: alt-sohbet → ana oturum eşlemesi, istem/tur izleme, kabuk dizini izleme (`shellcwd.py`), kendisini ajanın başlattığını tanıma. |
| `lib/orkestra_core/cursorkit.py` | Cursor skill/alt-ajan dosyalarını üretir (`plugins/orkestra/cursor/`), `~/.cursor`'a kurar, `hooks.json`'ı birleştirir. |

Cursor'da ölçülen ve tasarımı belirleyen davranışlar: her alt-ajan ayrı bir sohbettir ve olaylarında türü yoktur;
baskı kipinde (`agent -p`) istem ve durma olayı gelmez; kabuk dizinini çağrılar arasında korur ama olayda bildirmez;
sıfırdan farklı çıkan komut `postToolUseFailure` ile biter; alt-ajan bitişi için olay yoktur. Ayrıntı ve örnek yükler:
`CHANGELOG.md` (1.5.0), `tests/fixtures/cursor/`.

## Komutlar

Sohbet içinde: `/orkestra:run <istek>`, `/orkestra:status`, `/orkestra:resume`, `/orkestra:onboard`,
`/orkestra:rollback`, `/orkestra:ship` (commit + push; yalnız sen çağırırsın). Cursor'da aynıları tire ile:
`/orkestra-run` … `/orkestra-ship`.

Terminalde (`python3 ~/Projects/orkestra/plugins/orkestra/bin/orkestra …`; kısaltmak için README'deki `alias`):

| Komut | İş |
|---|---|
| `watch` · `dashboard --open` | Canlı görünüm (terminal · http://127.0.0.1:47616). Salt-okunur. |
| Bridge chat UI | http://127.0.0.1:47615/ · proje/Claude/Cursor seç; faz haritası, ajan ağı, görevler ve son hook hareketlerini izle. |
| `status -p <proje>` · `context` | Run'ın fazı, görevleri, kanıt eksikleri. |
| `next -p <proje>` | Sıradaki tek adım. Aynı durumda hep aynı cevap. |
| `tempo -p <proje> [--run <id>]` | Süre dökümü: fazlar, görev başına süre, inceleme retleri, ölü bekleme payı. |
| `watchdog [--dry-run\|--quiet\|--launchd]` | Oturum dışından tarama ve bildirim. |
| `agents [clear] -p <proje>` | Arka plandaki alt-ajan kayıtları. |
| `doctor` | Ortam ve hook öz-testi. |
| `run [--engine claude\|cursor] -p <proje> "<istek>"` | Seçilen motorun kendi arayüzünü projede açıp run'ı başlatır. |
| `cursor install \| uninstall \| status \| build` | Cursor tarafı: skill, alt-ajan ve hook kurulumu (senin `hooks.json` girdilerin korunur). |

Geri kalan alt komutları (`phase`, `task`, `tdd`, `gate`, `evidence`, `plan check`, `memory` …) orkestratör ve ajanlar
kullanır; elle çağırman gerekmez. Seçenekler tam yazılır; kısaltma (`--no` gibi) kabul edilmez.

## Dosyalar

Her projede `.orkestra/` oluşur:

- `features/NNN-slug/` — insanın okuduğu belgeler: `request.md`, `context.md`, `spec.md`, `plan.md`, `ledger.md`
  (her faz geçişi ve kanıt satırı), `tasks/`, `reviews/`, `verification.md`, `report.md`. Git'e girebilir.
- `runs/NNN-slug/` — makine durumu: `state.json`, `approval.json`, `agents.json`, `activity.jsonl`, kanıt logları,
  dosya yedekleri. Git'e girmez; yalnız CLI ve hook'lar yazar.
- `memory/` — proje hafızası: `PROJECT.md`, `FEATURES.md`, `specs/`, `DECISIONS.md`, `LEARNINGS.md`.
- `config.json` — projenin kapı komutları. `current` — açık run'a kısa yol.

Makinede `~/.orkestra/`: `config.json` (guard anahtarları, `watchdog` eşikleri), oturum–run bağları, loglar, bridge
ayarı. Model bu klasöre yazamaz.

## Guard'lar

Her Claude Code oturumunda: **git** (commit/push/branch yalnız o mesajda istenirse; alt-ajan raporu izin taşımaz),
**secrets** (`.env*`, anahtar dosyaları okunamaz ve yazılamaz), **store** (EAS/Expo yüklemeleri), **home**
(`~/.orkestra`), **rollback**, **complex** (denetlenemeyecek kadar büyük komut).

Yalnız run'a bağlı oturumda: **phase** (onaydan önce kaynak koda yazma yok), **isolation** (proje dışına yazma yok),
**deps** (onaysız paket yok), **deploy**, **roles** (salt-okur ajanlar yalnız rapor yazar), **internals** (makine
durumuna elle ya da betikle yazma yok; onay cevabı gömülemez), **wait** (alt-ajan için uyuma ve yoklama yok),
**dialogs** (ekranda sistem penceresi açan komut yok), **Stop** (run aktifken tur bitirilirse sıradaki adımla devam
ettirilir; alt-ajan çalışırken bitmesine izin verilir).

Bilinçli istisna: `/tmp/<klasör>` (ve `$TMPDIR` altı) izolasyondan muaftır, çünkü ajanların deneme ve kopya için bir
yere ihtiyacı var. Geçici kökün kendisi (`/tmp/*`) muaf değildir.

Dokuz guard `~/.orkestra/config.json` → `guards` altından kapatılabilir. `guards.test_syntax` ters yönde bir
seçenektir ve kapalı gelir: açılırsa `[[ a > b ]]` ve `(( n > 100 ))` yönlendirme değil karşılaştırma olarak okunur.
bash'e karşı doğrulandı, zsh'e karşı doğrulanmadı; o yüzden kapalı.

## İzleme

`watch` ve `dashboard` her run için fazı, o fazda geçen süreyi, görev ve kanıt ilerlemesini, kapı sonuçlarını, token
kullanımını, son araç çağrılarını ve **duraklama sınıfını** gösterir:

1. seni bekliyor (soru, onay, bloke, tur bitti) · 2. ölü bekleme (alt-ajan bitti, ana oturum ilerlemiyor) ·
3. asılı komut · 4. sessiz oturum (10 dakikadır kayıt yok) · 5. döngü (aynı ret ya da aynı komut 3 kez).

`watchdog` aynı sınıflandırmayı oturumun dışından yapar ve yeni olayda bir kez macOS bildirimi gönderir. Bridge
çalışıyorsa dakikada bir kendisi koşar; çalışmıyorsa `orkestra watchdog --launchd` bir LaunchAgent dosyası yazar.
Otomatik devam yalnız başsız başlatılmış run'lar içindir (en çok 3 kez, aynı oturumda, o oturumun süreci yaşarken
asla). Etkileşimli pencereden yürüttüğün run kendiliğinden sürdürülmez.

## Süre ölçümü ve tempo

4–5 Ekim'de 1.2.1 ile koşan beş run'da ölçülenler (`orkestra tempo`):

| Run | Süre | Görev | İnceleme reddi | Ölü bekleme payı |
|---|---|---|---|---|
| game-app · kalan işler | 12 sa 37 dk | 28 | 17 | %29 |
| deals-app · derin analiz | 6 sa 57 dk | 57 | 67 | %19 |
| mobile-app 001 | 5 sa 55 dk | 27 | 13 | %34 |
| mobile-app 002 | 5 sa 58 dk | 28 | 19 | %26 |
| mobile-app 003 | 11 sa 2 dk (5 sa 18 dk'sı onay beklemesi) | 62 | 0 | %12 |

İki büyük kayıp vardı. Birincisi ölü bekleme: Claude Code alt-ajanı arka planda çalıştırıp raporu yeni mesajla
getirirken Stop hook'u turun bitmesini engelliyor, model de `sleep` döngüsüyle bekliyordu. 1.4.0'da ajan çalışırken
tur bitebilir, bekleme komutları reddedilir. İkincisi gereksiz inceleme turu: 116 reddin 35'i yalnız küçük notlar
içindi. Artık PASS alan görev yeniden açılmaz, notlar final incelemeye taşınır, üçüncü FAIL sana sorulur.

Bu değişikliklerin süreye etkisi henüz ölçülmedi. 1.4.0 ile koşan ilk run'dan sonra `orkestra tempo` aynı tabloyu verir;
karşılaştırma oradan yapılır.

Kesintisiz devam: yarıda kalan run için ayrı bir "nerede kaldım" kaydı yoktur. `orkestra next` cevabı state, kanıt ve
inceleme dosyalarından hesaplar; oturum kapansa, bağlam sıkışsa ya da yeni pencere açsan da aynı alt-adımı bulur.
`/orkestra:resume` bunu kullanır.

## Kapsam

Orkestra yalnız Claude Code oturumlarında çalışır ve tam akış ancak bir run başlatıldığında devreye girer.

- `/orkestra:run` ile ve bridge üzerinden gelen istek: tam akış.
- Claude Code'da run başlatmadan yazılan istek: yalnız "her oturumda" guard'ları. Plan, onay ve TDD yoktur.
- Açık run varken aynı projede run'a bağlı olmayan ikinci oturum: uyarı alır ama dosyaları değiştirebilir.
- Cursor'ın kendi ajanı, Codex, Gemini CLI, Copilot, claude.ai sohbeti ve Cowork: kapsam dışı.

## Koordinasyon

Bu klasörü üç agent günceller: orkestra-core (eklenti ve bridge), openclaw-agent (`integrations/openclaw/`),
ecc-karpathy (`vendor/ecc`, `vendor/karpathy`, isteğe bağlı playbook'lar, bu belge). Kurallar `CONTRIBUTING.md`'de;
en önemlileri: açık run varken eklenti dosyası değiştirilmez; değişiklikten önce yedek ve md5 alınır, iş ayrı bir
kopyada yapılıp testler orada koşulur, canlı klasöre tek seferde aktarılır; commit, push ve branch yalnız depo sahibinin açık isteğiyle olur.

CLI'ye yeni seçenek ya da alt komut eklenirken tek yer `lib/orkestra_core/cliparse.py`'dir: guard'lar komut satırını
oradaki ayrıştırıcıyla okur.

## Bakım ve geri alma

Test:

```bash
cd ~/Projects/orkestra/plugins/orkestra/tests && python3 -m unittest discover -s . -q   # 357 test
cd ~/Projects/orkestra/bridge/tests && python3 -m unittest discover -s . -q             # 19 test
cd ~/Projects/orkestra/integrations/openclaw/plugin && npm test                          # 15 test
python3 ~/Projects/orkestra/plugins/orkestra/bin/orkestra doctor
```

Eklenti yerinde yüklendiği için kod değişikliği için kurulum gerekmez: hook'lar ve CLI her çağrıda diskteki dosyayı
okur. Ajan ve skill metinleri ile `hooks.json` yeni açılan Claude Code oturumunda geçerli olur. Bridge uzun yaşayan bir
süreçtir; bridge kodu değiştiyse yeniden başlatılır. `install.sh` yalnız ilk kurulum, MCP ekleme ve testleri bu
makinede koşmak için gerekir.

Bir sürümü geri almak (açık run yokken): depoda her sürüm bir etikettir.

```bash
python3 plugins/orkestra/bin/orkestra cursor uninstall   # yalnız 1.5.0'ın altına inerken (Cursor desteği 1.5.0 ile geldi)
git fetch --tags && git checkout v1.4.0                  # istenen sürüm
cd plugins/orkestra/tests && python3 -m unittest discover -s . -q
```

Depoyu git ile almadıysan aynı şey o sürümün arşiviyle yapılır (GitHub "Releases"). Bir run'ın proje
dosyalarında yaptığı değişikliği geri almak ayrı bir iştir: `/orkestra:rollback`.

Sabitlenmiş referansları yenilemek: `bash install.sh --refresh-vendor` (kaynak ve commit'ler `vendor/PINNED.md`'de).

## Açık konular

- CI matrisi Python 3.9, 3.13 ve 3.14 için Linux/macOS'ta yapılandırıldı; tam yerel test paketi Python 3.14.7'de koştu.
- Cursor motoru komut satırı aracıyla (`agent` 2026.10.01) beş gerçek XS run'da denendi; Cursor IDE penceresi ayrıca
  denenmedi (aynı hook sözleşmesi; IDE'ye özgü bir fark gözlenmedi çünkü bakılmadı).
- Cursor, Claude Code'un eklenti kaydındaki eski orkestra kopyasını (1.0.0, `~/.claude/plugins/cache/…`) da yükler;
  o kopyanın hook'ları Cursor sohbetlerinde eski kurallarla cevap verir (ör. `klasör/**` Grep'ini reddeder).
  `claude plugin update orkestra@orkestra-local` kaydı yeniler; Claude Code'un yerinde yüklemesine etkisi denenmedi.
- Kendi kendini onaylama gerçek bir run'da görüldü ve üç katmanla kapatıldı (CHANGELOG 1.5.0); ortamını temizleyip
  üst süreçlerinden kopan, adları gizleyen bir betik hâlâ aşabilir. Claude Code tarafında hook'un "beni ajan başlattı"
  denetimi yok (Claude Code'un süreç düzeni ölçülmedi); komut ve içerik denetimi iki motorda da var.
- Cursor run'larında token sayımı ve implementer için bitiş anındaki TDD kanıtı uyarısı yok.
- Token maliyeti (run başına 76–546 milyon cache okuma) ölçülüyor ama azaltılmadı.
- Kapsam boşlukları yukarıdaki gibi duruyor.
