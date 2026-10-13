"""Run the real script of the served dashboard page in node (dash_harness.js): a small DOM, a controllable clock and a
fake server. Shared by test_i18n_monitor.py (the page in both languages) and test_dashboard_ux.py (behaviour)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

HARNESS = Path(__file__).resolve().parent / "dash_harness.js"
HAVE_NODE = bool(shutil.which("node"))


def run_dashboard(workdir: Path, page: str, views: List[Dict[str, Any]], steps: Optional[List[Dict[str, Any]]] = None,
                  headers: Optional[Dict[str, str]] = None, build: str = "", fail: bool = False,
                  options: Optional[Dict[str, Any]] = None, settle_ms: int = 100) -> Dict[str, Any]:
    """Load `page` in the harness against a server that answers `views`, let it poll once, then run `steps`
    (see dash_harness.js). Returns the harness output: results, errors, calls, mutations, reloads."""
    opts = dict(options or {})
    if "now" not in opts and views:
        opts["now"] = int(float(views[0].get("now") or 0) * 1000) or None
    if opts.get("now") is None:
        opts.pop("now", None)
    page_file, scn_file = workdir / "page.html", workdir / "scenario.json"
    page_file.write_text(page, encoding="utf-8")
    head = {"X-Orkestra-Build": build, "X-Orkestra-Stale": "0"} if build else {}
    head.update(headers or {})
    lead: List[Dict[str, Any]] = [{"op": "set", "key": "views", "value": views}, {"op": "set", "key": "headers", "value": head}]
    if fail:
        lead.append({"op": "set", "key": "fail", "value": True})
    lead.append({"op": "advance", "ms": settle_ms})
    scn_file.write_text(json.dumps({"page": str(page_file), "options": opts, "steps": lead + list(steps or [])},
                                   ensure_ascii=False), encoding="utf-8")
    cp = subprocess.run(["node", str(HARNESS), str(scn_file)], capture_output=True, encoding="utf-8", timeout=120,
                        env=dict(os.environ))
    if cp.returncode != 0:
        raise AssertionError("node failed: " + cp.stderr[-2000:])
    return json.loads(cp.stdout)


def card_texts(snap: Dict[str, Any], views: List[Dict[str, Any]]) -> Dict[str, str]:
    """{run_id: text of its card or compact row} from a harness snapshot."""
    by_key = {v["key"]: v["run_id"] for v in views if "key" in v}
    out: Dict[str, str] = {}
    for sec in snap["sections"]:
        for item in sec["items"]:
            out[by_key.get(item["key"], item["key"])] = item["text"]
    return out
