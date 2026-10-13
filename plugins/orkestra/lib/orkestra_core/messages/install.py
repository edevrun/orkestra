"""install: the output of the cross-platform installer `install.py` (English + Turkish). Keys are `install.<name>`;
both dicts must hold the same keys and {fields}. `install.sh` carries its own pairs (it has no Python at hand)."""
from __future__ import annotations

from typing import Dict

EN: Dict[str, str] = {
    "install.description": "Install Orkestra (cross-platform)",
    "install.help_yes": "non-interactive defaults",
    "install.help_skip_tests": "skip the plugin and bridge test suites",
    "install.help_no_mcp": "accepted and ignored: only install.sh has MCP steps",
    "install.windows_note": "Note: LaunchAgent / launchd steps are macOS-only and were skipped.",
    "install.finished": "Orkestra install.py finished.",
}

TR: Dict[str, str] = {
    "install.description": "Orkestra'yı kur (platformlar arası)",
    "install.help_yes": "etkileşimsiz varsayılanlar",
    "install.help_skip_tests": "eklenti ve bridge test takımlarını atla",
    "install.help_no_mcp": "kabul edilir ve yok sayılır: MCP adımları yalnız install.sh'de vardır",
    "install.windows_note": "Not: LaunchAgent / launchd adımları yalnız macOS içindir ve atlandı.",
    "install.finished": "Orkestra install.py tamamlandı.",
}
