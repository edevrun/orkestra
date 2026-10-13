"""runs: human-facing messages (English + Turkish). Keys are `runs.<name>`; both dicts must hold the same keys and {fields}."""
from __future__ import annotations

from typing import Dict

EN: Dict[str, str] = {
    # memory/FEATURES.md, written only when the file has no table yet (the template normally provides it)
    "runs.features_header": ("# Features\n\nOne line per delivered orkestra run (newest last). Details live in "
                             "`.orkestra/features/<id>/`.\n\n| id | title | date | capabilities | summary |\n"
                             "|---|---|---|---|---|\n"),
    # `orkestra gate baseline|verify` table header
    "runs.gate_table_header": "| gate | baseline | now | time | command |",
    # `gate verify` footers: gates not executed because they already failed at baseline / carried from an earlier verify
    "runs.gates_unprotected": "gates NOT protecting this run (failed at baseline, not re-run; `gate verify --all` runs them): {names}",
    "runs.gates_carried": "gates carried forward (no file in their paths changed since their last PASS): {names}",
    # activity row when a run changes status: who asked (a chat id, or cli), from, to
    "runs.status_row": "{by}: status {src} → {dst}",
}

TR: Dict[str, str] = {
    "runs.features_header": ("# Features\n\nTeslim edilen her orkestra run'ı için bir satır (en yenisi en sonda). "
                             "Ayrıntılar `.orkestra/features/<id>/` altında.\n\n"
                             "| id | başlık | tarih | yetenekler | özet |\n|---|---|---|---|---|\n"),
    "runs.gate_table_header": "| gate | baseline | now | süre | komut |",
    "runs.gates_unprotected": "Bu run'ı KORUMAYAN gate'ler (baseline'da başarısız, yeniden çalıştırılmadı; `gate verify --all` çalıştırır): {names}",
    "runs.gates_carried": "Önceki sonuçtan taşınan gate'ler (son PASS'ten beri paths içinde dosya değişmedi): {names}",
    "runs.status_row": "{by}: durum {src} → {dst}",
}
