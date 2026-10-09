---
feature: {{run_id}}
risk: low
baseline_head: {{head}}
approved_dependencies: []
---
# Plan — {{title}}

> NASIL. Görevler kullanıcı onayından sonra `orkestra` tarafından state'e yüklenir.
> Onaydan sonra bu dosyada yapılan her sapma en alttaki günlüğe yazılır ve teslim raporunda diff olarak gösterilir.

## Yaklaşım
- Seçilen: <mimari yaklaşım, 3-5 cümle>
- Değerlendirilen alternatifler: <A — neden değil>, <B — neden değil>

## Kod haritası
| Dosya | Değişiklik | Neden / izlenecek mevcut pattern |
|---|---|---|
| `src/...` | değişir | `src/benzer.ts:40-80` pattern'ini izle |

## Arayüzler ve sözleşmeler
<Yeni/değişen fonksiyon imzaları, API şekilleri, tipler, event'ler. Tüketen/üreten kim.>

## Genel kısıtlar
- Mevcut public API/davranış bozulmaz (baseline gate'leri yeşil kalır).
- İstek dışı refactor, format değişikliği, bağımlılık yok.
- Kod yorumları İngilizce; kullanıcıya dönük metinler projenin dilinde.

## Görevler

### T001 — <kısa başlık>
- Dosyalar: `src/a.ts` (değişir), `src/a.test.ts` (yeni)   ← var olmayan her dosya `(yeni)` diye işaretlenir; CLI kontrol eder
- Bağımlı: —
- Kabul: Given <durum> When <eylem> Then <sonuç> (FR-001)
- Test: `<tek testi koşan dar komut>`
- TDD: required

### T002 — <kısa başlık>
- Dosyalar: `...`
- Bağımlı: T001
- Kabul: ... (FR-002, E1)
- Test: `...`
- TDD: required

## Test stratejisi ve izlenebilirlik
| Gereksinim | Test | Tür |
|---|---|---|
| FR-001 | `a.test.ts › ...` | unit |
| SC-001 | `...` | integration/e2e/manual-UI |

## Doğrulama kapıları
- Baseline'da geçen tüm gate'ler geçmeye devam eder (`orkestra gate verify`).
- Ek: <UI doğrulaması / manuel senaryo / performans ölçümü varsa>

## Riskler ve geri alma
- Risk: <...> → Önlem: <...>
- Geri alma: `/orkestra:rollback` (dosya yedekleri; git yazımı yok)

## İstisnalar
<Normalde YOK. Yalnız kullanıcı açıkça isterse: test silme/skip ya da lint/type bastırma gereken dosya — `yol` + gerekçe.
Bu bölüm onaylı plandan okunur; onaydan sonra eklenen istisna geçersizdir.>

## Yeni bağımlılıklar
<Yoksa "Yok". Varsa her biri: paket — neden — alternatif — lisans. Frontmatter'daki approved_dependencies listesine de eklenir; kullanıcı onayıyla geçerli olur.>

## Plan değişiklik günlüğü
<!-- append-only: - YYYY-MM-DD HH:MM — ne değişti — neden -->
