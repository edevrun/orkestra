# Changelog

## Unreleased
- Güncel Claude kaynaklarından altı bayat Cursor skill'i yeniden üretildi.
- OpenClaw eklentisinin Node testleri Linux ve macOS CI iş akışına eklendi.
- Bridge'e token bellekte tutulan, loopback-only yerel chat arayüzü ve proje/motor keşfi eklendi.
- Yerel arayüze gerçek run verisinden responsive faz haritası, etkin ajan ağı, görevler, kalite kapıları ve son hook hareketleri eklendi.
- README kurulum adresi ve faz listesi kodla eşitlendi; CI Python matrisi 3.14'e genişletildi (3.9 minimumu korunarak).
- Herkese açık depo testi, köke sabitlenmiş `dist/`/`internal/` ignore kurallarını davranış olarak doğruluyor.

## 1.6.0 — 2026-10-06
Depoyu herkese açmaya hazırlık (dört açıdan denetim, her bulgu ayrıca çürütülmeye çalışıldı) ve zayıf bir modelin
yanlış yapabildiği dört yerin CLI'a alınması.
- **Lisans ve kaynaklar:** `THIRD_PARTY_NOTICES.md` (kökte ve eklenti klasöründe): dokuz kaynak projenin adresi,
  sabitlenen commit'i, lisansı, telif satırı ve klasördeki lisans dosyası. humanlayer (Apache-2.0) için lisansın tam
  metni eklendi (`LICENSE-APACHE-2.0.txt`; upstream'in kendi LICENSE'ı yalnız başlıktı, NOTICE dosyası yok).
  karpathy-skills için upstream'de LICENSE yok: MIT beyanını taşıyan `plugin.json` kanıt olarak eklendi, açıklamalı
  LICENSE yazıldı. Eklenti klasörüne Orkestra'nın kendi LICENSE'ı kondu. Bir test her vendor klasörünün lisansını ve
  bildirimdeki satırını denetler.
- **Kişisel veri:** manifestlerden e-posta çıkarıldı; özel proje adları ve run başlıkları genel adlarla değiştirildi;
  iç çalışma notları (`COORDINATION.md`, `integrations/ecc-karpathy/`, `integrations/openclaw/NOTES.md`) `internal/`
  altına taşındı ve `.gitignore`'a eklendi. Bir test e-posta, ev dizini yolu, sohbet kimliği ve bot token'ı arar.
- **Yabancı biri için:** İngilizce `README.md` (Türkçe belge `README.tr.md`), `CONTRIBUTING.md`, `SECURITY.md`,
  GitHub Actions iş akışı (henüz GitHub'da koşmadı), sürüm geri alma arşiv yerine etiketle. Varsayılan çalışma klasörü
  tek yerde; bridge istemcisi ve OpenClaw eklentisi kendi bulundukları kopyayı kullanır.
- **`task done` (M/L):** bu turun PASS review'u yoksa ya da review FAIL diyorsa CLI reddeder (playbook zaten böyle
  diyordu; zayıf bir orkestratör reviewer'ı hiç çağırmadan görevi kapatabiliyordu). XS/S ve sonradan eklenen görevler
  muaf. Kapatmak için: `~/.orkestra/config.json` → `tempo.task_review_required: false`.
- **`orkestra playbook`** adsız çağrılınca run'ın fazını kullanır; yanlış fazın playbook'u istenirse başına not koyar.
- **Başsız run:** her playbook'un başına "turu bitirme, `sleep 30; orkestra next` ile bekle" satırı gelir
  (playbook'lar "turu bitir" diyordu; başsız süreçte bu alt-ajanları öldürür).
- **`orkestra brief`** düzeltme turunda review dosyasının yolunu verir.
- Testler: 344 plugin + 16 bridge.

## 1.5.2 — 2026-10-06
Kurulumu (`install.sh`) elle çalıştırınca durduran iki şey; ikisi de yük ve terminalle ilgiliydi, akışla değil.
- Testlerdeki beş "doğrusal kalmalı" süre sınırı duvar saatinden CPU süresine geçti (`helpers.assert_cpu_under`;
  makinenin o anki yavaşlığı sabit bir döngüyle ölçülür, bütçe en çok 6 kat genişler). Yük altında biri 1,5 sn
  yerine 1,82 sn ölçüp `install.sh`'ı durdurmuştu; kod değil ölçüm yöntemi yanlıştı.
- Bridge `doctor`: `claude plugin list` klavyeyi (stdin) devralıyordu; terminalden elle çalıştırılan `install.sh`'ta
  60 sn bekleyip hatayla ölüyordu (borudan çalışınca görünmüyordu). stdin kapalı başlatılır, cevap gelmezse satır
  olarak yazılır. Aynı düzeltme `orkestra doctor`'daki `claude mcp list` ve `agent create-chat` çağrılarında.

## 1.5.1 — 2026-10-05
Cursor hook'u hafifletildi. Olay: Cursor IDE'de `/orkestra-run …` gönderildi, makine aşırı yüklüydü (yük ortalaması
230–385; aynı anda üç Playwright koşusu), kullanıcının kendi `safety-gate.py` hook'u 8 sn'lik süresini aşıp
`failClosed` olduğu için gönderimi engelledi. Orkestra engellemedi, ama o anda o da ağırdı: ilk istem için 5 `ps`
çağrısı + bütün modüller (ölçüm: o yük altında 7–19 sn; Cursor 15 sn'de kesti) ve aynı anda başlayan diğer hook'lara
yük bindiriyordu.
- **Hafif yol:** hiçbir run'ın tanımadığı bir sohbetin istemi, stop'u ve alt-ajan başlangıcı artık orkestra
  modülleri yüklenmeden ve süreç listesi okunmadan cevaplanır (yalnız küçük `intent` modülü). Orada onaylanacak,
  dürtülecek ya da kaydedilecek bir şey yoktur; geriye kalan defter işi yalnız bir şeyi GERİ ALIR (eski git izni,
  alınmamış not), bu yüzden köken denetimi gerekmez. İzin VEREN istem ("commit et"), sürücü duyurusu ve bir run'ın
  tanıdığı sohbetteki her şey eskisi gibi tam yoldan geçer. Ölçüm (yüksüz): ilk istem 138 → 57 ms, stop 136 → 35 ms,
  `ps` çağrısı 5 → 0.
- `subprocess`, `traceback`, `uuid` yalnız kullanıldıkları yerde yüklenir (bu betik her Cursor olayında başlar).
- Hook süreleri: 15 → 30 sn (sessionStart 20 → 30; subagentStart/sessionEnd 10 → 15). Süresi dolan hook "cevap yok"
  sayılır, yani çalışmamış bir guard'dır. Uygulamak için `orkestra cursor install` bir kez daha çalıştırılır.
- Denenip bırakılan: köken denetimini tek `ps -A` çağrısına indirmek — aynı yük altında tek çağrı 6–10 sn, süreç
  başına `ps -p` 0,1–0,8 sn sürdü; olduğu gibi kaldı.
- Testler: 336 plugin + 16 bridge (yeni: `TestLightPrompts`, 6 test; 13 mutasyonun 13'ü yakalandı).
- Orkestra'nın dışında kalan: `safety-gate.py` (8 sn, `failClosed`) aynı yük altında tek başına ~6 sn sürüyor.

## 1.5.0 — 2026-10-05
İki motor: bir run'ı Claude Code (varsayılan) ya da Cursor sürer; motor istek başına seçilir. Durum makinesi, kapılar,
kanıt ve guard'lar tektir — Cursor için ayrı bir kopya yok, yalnız bir çevirmen var.
- **Seçim:** terminal `orkestra run [--engine claude|cursor] -p <proje> "<istek>"` (o motorun kendi arayüzünü projede
  açar); Telegram `/yap cursor <proje> <istek>` (`cursor:` / `claude` da olur); bridge API'sinde `"engine": "cursor"`;
  Cursor sohbetinde `/orkestra-run <istek>`. Bir şey yazılmazsa Claude. Varsayılanı değiştirmek:
  `~/.orkestra/config.json` → `engine.default`. Run'ın motoru `state.json`, `request.md`, `status`, `watch` ve panelde.
- **`orkestra cursor install | uninstall | status | build`:** skill'leri (6) ve alt-ajanları (8) Claude kaynaklarından
  üretir (`plugins/orkestra/cursor/`, test bayatlamayı yakalar), `~/.cursor`'a bağlar, hook girdilerini
  `~/.cursor/hooks.json`'a **senin girdilerine dokunmadan** ekler (önce zaman damgalı yedek; bozuk ya da tanımadığı
  biçimde dosyaya hiç dokunmaz; symlink ise hedefine yazar) ve `orkestra` komutunu PATH'e koyar. Yinelenebilir.
- **`hooks/cursor_hook.py`:** Cursor olaylarını (sessionStart, beforeSubmitPrompt, preToolUse, postToolUse,
  postToolUseFailure, stop, subagentStart, sessionEnd) `dispatch.py`'nin aynı işleyicilerine çevirir, cevabı Cursor
  biçimine döndürür. Cursor'un gerçek davranışı ölçülerek yazıldı (`agent` 2026.10.01; örnek yükler
  `tests/fixtures/cursor/`):
  - Oturum `cursor-<sohbet kimliği>` olarak bağlanır. Her alt-ajan ayrı bir sohbettir ve olaylarında ajan türü yoktur:
    ana oturumun `Task` çağrısı kaydedilir, alt sohbet ilk mesajındaki Task metniyle eşlenir. İlk çağrıları transkript
    oluşmadan gelir; o sırada bekleyen başlatmanın rolüyle (birden çok tür bekliyorsa en kısıtlısıyla) değerlendirilir,
    kalıcı kayıt yalnız kesin eşleşmede yazılır. Run'ın projesi Cursor'un açıldığı klasör olmak zorunda değil.
  - Soru ve plan onayı: Cursor'da AskUserQuestion yok; başsız protokol geçerli (`orkestra await`, sen `onay` yazarsın,
    hook kaydeder). Baskı kipinde (`agent -p`) istem ve durma olayı gelmediği için bridge ve watchdog istemi hook
    katmanına kendileri bildirir (`engines.driver_prompt`); ajan kabuğundan gelen böyle bir bildirim reddedilir.
  - "O mesajda izin" (commit/push) ve tur başına en çok iki devam uyarısı kullanıcı istemine bağlıdır; Cursor aynı
    istem içinde üretim kimliğini değiştirdiği için tur ayrı izlenir. Oturum bitince izin de biter.
  - Cursor'un kabuğu çağrılar arasında dizinini korur ve olaylarda dizin gelmez: `cd` komut komut izlenir
    (`shellcwd.py`); izlenemeyen durumda (`cd "$X"`, `cd -`, pushd, alias/fonksiyon tanımı, `a || cd x` …) sonraki
    komut `cd <mutlak yol> && …` ile başlamak zorundadır. `make && cd build` gibi koşullu bir `cd`, komut sıfırla
    dönene kadar "bilinmiyor" sayılır. Göreli yol artık kabuğun gerçekten bulunduğu yere göre değerlendirilir; bir
    run'ın durum dosyalarıyla aynı adı taşıyan göreli yollar (`state.json`, `approval.json`, `evidence/…`) Cursor'da
    tam yolla yazılmak zorundadır.
  - Alt-ajanlar ön planda çalışır; Cursor bitişte olay göndermediği için dönüş, ana oturumun sonraki hamlesinden anlaşılır.
  - Bir orkestra run'ıyla ilgisi olmayan Cursor sohbetlerine dokunulmaz (hiçbir orkestra modülü yüklenmeden cevaplanır).
    Bir run sürmüş sohbet, run kapandıktan sonra da her-oturum guard'larına (git, sır, store) tabidir. Hepsini tüm
    Cursor sohbetlerine açmak: `engine.cursor.global_guards: true`.
- **Bridge / watchdog:** iş başına motor; Cursor işi kendi sohbetinde sürer (`agent create-chat`, `--resume`), duran iş
  aynı sohbette devam ettirilir. Hook'lar kurulu değilse Cursor turu başlatılmaz (guard'sız ajan çalışmaz) — her turdan
  önce bakılır. `doctor` Cursor satırı ekler. Claude komut satırı ve istemleri 1.4.0 ile aynıdır.
- **Kendi kendini onaylama (gerçek bir run'da görüldü, iki motor için kapatıldı):** istekte "soru sormana gerek yok"
  yazan bir denemede Cursor modeli onay kapısında orkestranın testlerini okudu, oradaki yükü bir `python3 <<'PY'`
  betiğine kopyalayıp `hooks/cursor_hook.py beforeSubmitPrompt`'u `{"prompt": "onay"}` ile kendisi çalıştırdı ve
  uygulamaya geçti. Üç katman eklendi: (1) bağlı oturumda orkestranın hook giriş noktalarını ya da `orkestra_core`'u
  **adlandıran** bir komut yalnız okuyabilir (grep, cat); çalıştıran ya da yazan komut ve bu adları içeren dosya içeriği
  (Write/Edit) `internals` ile reddedilir — orkestra deposunun kendisi hariç; (2) Cursor hook'u kendisini ajanın
  başlattığını tanır (ortamında ajan kabuğunun işaretleri ya da üst süreçlerinde Cursor'un ajan-komutu sarmalayıcısı
  varsa hiçbir şey kaydetmez; `beforeSubmitPrompt`, `stop`, `sessionEnd`, `postToolUse` dahil); (3) onay playbook'u,
  başsız playbook ve `phase next` reddi bunun kullanıcının rızasını taklit etmek olduğunu ve "sorma" demenin onayı
  kaldırmadığını açıkça söyler. Kaydedilen komut regresyon testidir. Sınır: ortamını temizleyip üst süreçlerinden
  kopan, adları gizleyen bir betik bunları aşar — guard'lar bir kum havuzu değildir.
- **Diğer guard değişiklikleri (iki motor):** Grep'te `klasör/**` deseni artık "sır dosyası hedefliyor" sayılmıyor
  (`*` ve `*.*` gibi; `.env*`, `**/.*` hâlâ ret). `/orkestra-ship` ve `/orkestra-rollback` (Cursor adları) aynı
  izinleri verir. Bozuk (UTF-8 olmayan) durum dosyası artık "yok" sayılır, hook'u düşürmez.
- **Metinler:** playbook ve skill'ler tek kaynaktan; Cursor'a verilirken adlar çevrilir, yalnız Claude Code'da doğru
  olan cümleler (arka plan ajanı, "turu bitir", SendMessage, /compact) Cursor karşılığıyla değişir
  (`engines.CURSOR_REWRITES`; kaynak cümle değişirse test kırılır).
- **Doğrulama:** gerçek Cursor CLI ile beş XS run (dördü bridge üzerinden başsız, biri terminal arayüzünde): onay
  öncesi kaynak yazımı ve izinsiz commit reddi, yazılan `onay`ın kaydı, implementer / verifier / reviewer alt-ajan
  rolleri, devam uyarısı, kabuk dizini izleme. Dördüncü run yukarıdaki kendi kendini onaylamayı ortaya çıkardı;
  düzeltmeden sonra aynı istekle beşinci run onayı bekledi. Bağımsız inceleme iki turda yapıldı: ilk tur 1
  engelleyici + 7 önemli, ikinci tur (düzeltmeler ve yeni katmanlar üzerinde) 1 önemli (başarısız olan bir `&&`
  zincirinden sonra dizinin yanlış bilinmesi) + küçük bulgular verdi; hepsi düzeltildi ve teste bağlandı
  (38 mutasyonun 38'i testlerce yakalandı). Testler: 330 plugin + 16 bridge
  (Python 3.9, 3.10, 3.14).
- **Bilinen sınırlar:** Cursor IDE penceresi doğrudan sürülmedi (CLI ile aynı hook sözleşmesini kullanır; IDE'ye özgü
  davranış gözlenmedi). Cursor'da implementer için "yeşil kanıt olmadan bitirme" uyarısı yok (alt-ajan bitiş olayı
  gelmiyor; `task done` kapısı aynen geçerli). Cursor run'larında token sayımı yok. Cursor, Claude Code'un eklenti
  kaydındaki orkestra kopyasını da yükler; o kopya eskiyse `orkestra cursor status` söyler.

## 1.4.0 — 2026-10-05
Hız, tempo denetimi ve kesintisiz devam.
Ölçüm (4–5 Ekim, 5 run): duvar saatinin %12–34'ü "alt-ajan bitti, ana oturum bekleme döngüsünde" geçmiş; 116 yeniden
açmanın 35'i yalnız minor içindi; bir onay "Onayla + ek kapsam" seçimi yüzünden ret yazılmıştı.
- **Ölü bekleme (A1):** Claude Code alt-ajanları arka planda çalıştırıyor ve raporu yeni mesajla getiriyor. `Stop` hook'u
  artık alt-ajan çalışırken turun bitmesine izin verir (eskiden engelliyordu; model de `sleep`/python döngüsüyle
  bekliyordu). Yeni `wait` guard'ı ana oturumun bekleme/yoklama komutlarını reddeder. Alt-ajan kaydı:
  `.orkestra/runs/<run>/agents.json` (hook'lar yazar; `SendMessage` ile devam ettirilen ajan da izlenir).
  Başsız (`claude -p`) çalışmalarda eski davranış korunur.
- **`orkestra next` (U1):** sıradaki tek adım; görev alt-adımı (started · red · green_stale · review_pending ·
  review_fail_rN · review_pass) state + kanıt + inceleme dosyasından türetilir. `context`, `resume`, `status`,
  SessionStart notu, Stop mesajı ve alt-ajan raporu geldiğinde eklenen bağlam bunu kullanır.
  `task start` / `task done` idempotent: yeniden çağrı hiçbir şeyi iki kez yazmaz; bitmiş görev `task start` ile açılamaz.
- **Tempo (U3):** `task done` / `phase next` çıktısında tek satır (`tempo: …`), bayrak varsa ne yapılacağıyla birlikte.
  `orkestra tempo`: faz ve görev süreleri, ret sayısı, ölçülen ölü bekleme payı.
- **Duraklama sınıfları (U2):** 1 kullanıcı · 2 ölü bekleme · 3 asılı komut · 4 sessiz oturum · 5 döngü. `watch` ve panel
  gösterir. Eşikler: `~/.orkestra/config.json` → `watchdog`.
- **`orkestra watchdog` (U4):** oturum dışı tarama + bir kez bildirim; sessiz başsız run'ı aynı oturumda devam ettirir
  (`auto_resume`: off | headless | all; en çok 3, artan aralık; canlı süreç / bridge işi varken asla). Bridge: duran işi
  aynı oturumda `/orkestra:resume` ile en çok 3 kez kendisi sürdürür ve taramayı dakikada bir çalıştırır.
  İkinci oturum aynı run'a bağlanırsa uyarı (ilk oturum son 2 dk içinde çalıştıysa) ya da devralma kaydı düşer.
- **İnceleme turları (A2/A4):** `task reopen`, son inceleme `PASS` ise reddeder (minor'lar `task note` ile final
  incelemeye taşınır); üçüncü tur kullanıcının kararını ister; `--force "<gerekçe>"` ledger'a yazılır.
- **Onay (A3):** yalnız onay sorusunun cevabı değerlendirilir; aynı çağrıdaki diğer cevaplar bağlama aktarılır.
  `<agent-message` / `<task-notification` / `[Subagent hand-back]` istemleri kullanıcı mesajı sayılmaz (onay, ret, git
  izni, "kullanıcı cevap verdi" yok); aktivitede `handback` olarak görünür.
- **Doğruluk (B):** soru açıkken run `awaiting_user` olur ve arka plan ajanı satırları soruyu gizlemez; dosyaları çakışan
  iki görev birlikte başlatılamaz; red ile green arasında ÇALIŞAN (ya da o arada biten) başka görevin test dosyası
  red→green kontrolünde sapma sayılmaz (`foreign_tests_changed` + ledger + rapor notu; çoktan bitmiş bir görevin
  testini değiştirmek hâlâ `--why` ister); biten run'ın token değeri state'e yazılır ve panelde kalır.
- **Guard düzeltmeleri (C1):** `2>&1` artık argüman sanılmıyor (`orkestra --help 2>&1` → "orkestra 2", `npm i x 2>&1` →
  paket "2" hataları); macOS `sed -i ''` betiği dosya sanılmıyor; `/tmp/x/*` geçici sayılıyor (kökün kendisi
  `/tmp/*` değil); `.orkestra/runs` altındaki kanıt ve aktivite kayıtlarını OKUYAN betikler serbest (makine durumu —
  state/approval/agents/gates — için 1.3.0'ın sıkı testi sürüyor). Salt-okur ajan ret mesajı deneme için `/tmp`
  yolunu söyler.
- **Sistem penceresi (C2):** yeni `dialogs` guard'ı + implementer / architect / plan-critic / reviewer / verifier kuralı.
- **Diğer:** `orkestra start` iki açık run varken uyarır (A7); `activity.jsonl` dolunca eski satırlar
  `activity.1.jsonl`'a taşınır (A8); 20'den fazla görevli planda bölme önerisi notu (D1); `orkestra changes` çıktısında
  deterministik `playbooks:` satırı (D5); bağlı olmayan oturuma "bu oturum run'ı sürmüyor" notu (D3).
- **Bağımsız inceleme, iki tur (yayın öncesi; 1. tur: 1 engelleyici + 6 büyük + 9 küçük, 2. tur: 2 büyük + 8 küçük
  bulgu; hepsi testli düzeltildi):**
  - Alt-ajan kaydı run'ı park edemez: kayıt, başlatan oturumu taşır; başka oturum run'ı devralınca sayılmaz; oturum
    yeniden başlayınca (SessionStart, `compact` hariç) ve rapor mesajı gelince düşer; `silent_seconds` boyunca hiçbir
    alt-ajan izi yoksa "kayıp" sayılır (`next` normal adımı söyler, not düşer). `orkestra resume` izi olmayan kayıtları
    düşürür; `orkestra agents [clear]` eklendi. `SendMessage` kaydı çağrı başarıyla döndükten sonra (PostToolUse) yazılır;
    arka plan başlatması yalnız yapısal olarak (`status: async_launched`) tanınır.
  - Başsız çalışmaya artık "turu bitir" denmez ("turun içinde bekle").
  - Güvenlik gerilemeleri kapatıldı: fd numarası atılınca yönlendirme hedefi kaybolmuyor (`exec 3>.env`, `true;2>dosya`,
    `;>dosya` artık yakalanıyor); betiklerle makine durumuna yazma biçimleri (`open(os.path.join(…),'w')`, `Path.open('w')`,
    `os.open`, `mode='w'`, perl/ruby/node) yeniden reddediliyor; CLI seçenek kısaltmaları kapalı (`allow_abbrev=False`) ve
    alt-ajanlar için bilinmeyen seçenek reddediliyor (`phase --no show next` ile `phase next` çalıştırılamıyor).
  - Önceden beri açık olanlar da kapandı: `orkestra evidence <etiket> <komut>` (`--` olmadan) içindeki komut artık
    guard'lardan geçiyor; `rollback --yes` konumdan bağımsız yakalanıyor; macOS'ta büyük/küçük harf farkıyla
    (`.ENV`, `.ORKESTRA/runs/…`) guard atlatılamıyor; AskUserQuestion girdisinin hiçbir yerine `answers` gömülemiyor.
  - `orkestra next`: yeniden açılan TDD'siz görev eski rapor yüzünden bitmiş sayılmıyor; son notu `BLOCKED` olan görev
    için karar isteniyor; yalnız `PASS` / `PASS_WITH_NOTES` geçer sayılıyor; `phase back implement` sonrası açık görev
    yoksa düzeltme görevi eklenmesi isteniyor (ping-pong yok).
  - Stop hook'u alt-ajan durdu ama raporu henüz gelmediyse dürtmüyor (çifte iş); hook'un yazdığı `awaiting_user` soru
    kapanınca kalkıyor; iki bind işaretinde tek JSON; rapor mesajı git iznini her durumda sonlandırıyor; yeniden onayda
    görev kayıtları (`review_seen`, `first_started`) korunuyor.
  - `wait` guard'ı: uygulama kodundaki `reviews/` yolu tetiklemiyor; salt bekleme yalnız kayıtlı alt-ajan varken
    reddediliyor; düzenli ifade doğrusal (17 KB'lık girdi 3,7 sn → < 0,1 sn).
  - `watchdog`: kendi başlattığı sürecin pid'i ve bağlı tüm oturum kimlikleri kontrol ediliyor; komut içinde ölen başsız
    oturum da sürdürülebiliyor. Bridge: tur sonu ile iptal yarışında iptal kazanıyor.
  - **Tek ayrıştırıcı (2. tur):** guard'lar `orkestra …` satırını artık CLI'nin kendi ayrıştırıcısıyla okur (yeni modül
    `orkestra_core/cliparse.py`); guard'ın gördüğü ile CLI'nin çalıştırdığı aynı şeydir. `evidence a <komut> --`,
    iç içe `orkestra evidence … orkestra task start …`, baştaki `--` gibi farklar kapandı; iç içe çağrılar sonuna kadar
    açılır (4 kat sınırı). `--` alt komuttan önce her Python sürümünde reddedilir.
  - "Başsız" tek yerde tanımlı (`paths.is_headless`: `mode == headless` ya da `ORKESTRA_HEADLESS=1`); bridge/watchdog'un
    sürdürdüğü etkileşimli run'da da Stop hook'u, bekleme guard'ı ve `next` aynı şeyi söyler.
  - Alt-ajan satırları ajan kimliğini (`aid`) taşır: aynı türden bir ajanın durması diğerinin çalışan komutunu
    kapatmaz. Kayıt, başka oturum yeni diye değil, başlatan oturum bir kira süresi (120 sn) sessizse düşer; aynı
    pencereden `resume` sessiz ajanı düşürmez; yolda olan her rapor ayrı sayılır (görev bildirimi rapor yerine geçmez).
  - Ayrıştırıcı: `>& dosya` yazma hedefidir; çalışma dizini `.orkestra/runs` olan ve heredoc ile beslenen betikler de
    denetlenir. Tek komutta 200'den fazla farklı yönlendirme hedefi ya da 2000'den fazla komut reddedilir; PreToolUse
    guard'ı 8 sn'de bitmezse hook zaman aşımına (= denetimsiz çalışma) bırakmak yerine açıkça reddeder (`complex`);
    büyük/küçük harfli özel `TMPDIR` geçici sayılır.
  - **Varsayılan KAPALI seçenek `guards.test_syntax`:** `[[ a > b ]]`, `(( n > 100 ))`, `$(( 5 > 3 ))` biçimlerini
    yönlendirme değil karşılaştırma olarak okur (salt-okur ajanlar ve onay öncesi fazlar bu biçimlerde boşuna
    engelleniyor — 1.3.0'dan beri böyle). Tanıyıcı dar tutuldu (yalnız komut konumundaki tırnaksız `[[ … ]]`; POSIX
    `sh`/`dash` içinde hiç; aritmetik gövdesi doğrusal denetleyiciyle) ve bash'e karşı doğrulandı; zsh'e karşı
    doğrulanmadığı ve bağımsız incelemenin son turu tamamlanamadığı için kapalı gönderiliyor. Kapalıyken davranış
    1.3.0'daki katı okumadır (yanlış ret olabilir, kaçan yazma olmaz).
- Testler: eklenti 112 → 228, bridge 6 → 9.

## 1.3.0 — 2026-10-05
ECC + karpathy-skills bağlaması (W1–W8; açık run yokken uygulandı):
- implementer: cerrahi diff, en sade kod, sessiz varsayım yok (karpathy).
- reviewer: sessiz hata avı, görevle izlenemeyen satır = bulgu; isteğe bağlı kontrol listeleri
  (`orkestra playbook security | stack-web | stack-mobile | data`) yalnız diff eşleşince yüklenir.
- adversary: derin listeler (`security`, `ui-flow`, `data`). architect: varsayımı yüzeye çıkar, önce mevcut kod /
  kurulu bağımlılık. plan-critic: FR'ye izlenemeyen görev = bulgu. verifier: `ui-flow`, `stack-mobile`.
- `run` skill: `/compact` yalnız faz sınırında önerilir. `vendor/PINNED.md`: ecc + karpathy satırları;
  `scripts/refresh-vendor.sh` artık `refresh-vendor-ecc.sh`'yi de çağırır.
- intake: hedef proje orkestra'nın kendisiyse katkı kuralları kuralı hatırlatılır.
- katkı kuralları: üç agent (orkestra-core, openclaw-agent, ecc-karpathy), sahiplik tablosu,
  "açık run varken plugin dosyası değiştirilmez" ve değişiklik öncesi yedek/md5 kuralı.
- Aşağıdaki 1.2.1 sonrası değişiklikler de bu sürüme dahildir:
- `watch`/`dashboard`: çalışmalar sabit sırada (açık olanlar önce, sonra proje adına göre) — son hareket edene göre
  yer değiştirmiyor.
- `watch`/`dashboard`/`watch --json`: çalışma başına token kullanımı (`usage`: girdi, çıktı, cache yazma, cache okuma,
  bağlam). Kaynak: bağlı oturumun ve alt-ajanlarının Claude Code kayıtları (`~/.claude/projects/…`); salt-okunur, yalnız
  sayılar okunur, çalışmanın başlangıcından itibaren sayılır. Yeni modül `orkestra_core/usage.py`, testler
  `tests/test_usage.py`. Hook'lar ve CLI akışı değişmedi (monitor yalnız `watch`/`dashboard` içinde yüklenir).
- Yeni, kimsenin çağırmadığı dosyalar: `vendor/ecc`, `vendor/karpathy`, 5 isteğe bağlı playbook, `scripts/refresh-vendor-ecc.sh`.

## 1.2.1 — 2026-10-04
- `watch`/`dashboard`: clarify/approve fazında hareket yoksa "muhtemelen SENİ BEKLİYOR" gösterilir (soru/onay
  penceresi açık kalmış olabilir).

## 1.2.0 — 2026-10-04
Canlı takip:
- Hook'lar her çalışmanın aktivite akışını yazar: `.orkestra/runs/<run>/activity.jsonl` (araç çağrıları, alt-ajan
  başlangıç/sonuçları, guard engellemeleri, sorular/cevaplar, tur sonu). Boyut sınırlı (512 KB), sır görünümlü değerler
  maskelenir, iş akışını asla bloklamaz.
- `orkestra watch [--once|--all|--json] [-p proje]`: tüm açık çalışmalar tek ekranda; "şu an ne yapıyor / seni mi
  bekliyor / oturum kapandı mı" tespiti.
- `orkestra dashboard [--port 47616] [--open]`: aynı görünüm tarayıcıda; yalnız 127.0.0.1, salt-okunur,
  DNS-rebinding korumalı.
- Bridge: Telegram `/durum` artık canlı durumu da gösterir; `GET /runs`; `orkestra_client.py runs` (eklemeli,
  kırıcı değişiklik yok).

## 1.1.2 — 2026-10-04
- Bridge varsayılan portu 8787 → 47615 (8787 ve 18787 game-app'in dev/e2e sunucularında). Eski 8787 ayarı
  otomatik taşınır.

## 1.1.1 — 2026-10-04
- Serena MCP düzenleme araçları (`safe_delete_symbol` dahil) orkestra guard'larına tabi; Serena ile yapılan
  düzenlemeler de yedeklenir ve rollback kapsamına girer.

## 1.1.0 — 2026-10-04
Kanıt katmanı (halüsinasyona ve "test oyunlarına" karşı deterministik kurallar):
- `plan check`: plandaki dosyalar gerçekten var olmalı; yeni dosyalar `(yeni)` ile işaretlenir.
- `tdd`: green, red ile aynı komutla koşulur; red sonrası test değişirse reddedilir (`--why` ile gerekçe raporda görünür);
  TDD görevi test dosyası değişmeden kapanmaz.
- `integrity check`: silinen test, eklenen skip/only/xfail, eklenen lint/type bastırma → verify geçmez
  (istisna yalnız onaylı planın `## İstisnalar` bölümünde).
- `verification.md`: var olmayan dosya/test adı gösterilemez; her gereksinimde doğrulanabilir referans;
  elle kontrol `orkestra evidence` logu ister.
- `report evidence --write`: raporun kanıt bölümü makine verisinden üretilir; elle değiştirilirse kapanış reddedilir.
- `orkestra tdd/evidence ... -- <komut>` içindeki komutlar da guard'lardan geçer.
- `.orkestra/current` kısa yolu (glob gerekmez).
Telegram / OpenClaw:
- `orkestra_bridge.py telegram-setup` (tek komut), `doctor [--live]`, mutlak claude yolu (launchd).
- `bridge/orkestra_client.py` (start/reply/status/wait/cancel) ve `integrations/openclaw/CONTRACT.md`.
- katkı kuralları: çok agent'lı çalışma kuralları (Telegram tek tüketici, sahiplik).

## 1.0.0 — 2026-10-04
İlk sürüm: faz makinesi, guard'lar, agent ekibi, gates, rollback, hafıza, bridge.
