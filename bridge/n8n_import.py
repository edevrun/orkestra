#!/usr/bin/env python3
"""Import the Orkestra n8n workflows into a running n8n instance.

Requires environment variables (no default path into a sibling private repo):

- ``N8N_URL`` — API base (default ``http://localhost:5678``)
- ``N8N_API_KEY`` — n8n API key, **or**
- ``N8N_STATE`` — path to a JSON file that contains ``{"apiKey": "..."}``

The bridge token is injected into the HTTP Request header. Workflow A (webhook → bridge)
is activated; workflow B (callback → Telegram) is imported inactive: pick a Telegram
credential, enable the node, activate.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "plugins" / "orkestra" / "lib"))
from orkestra_core.i18n import t  # noqa: E402  (the text a person reads follows ORKESTRA_LANG / config / locale)

N8N_URL = os.environ.get("N8N_URL", "http://localhost:5678").rstrip("/")
BRIDGE_CFG = Path(os.environ.get("ORKESTRA_HOME") or "~/.orkestra").expanduser() / "bridge.secret.json"

# Workflows imported before the English names existed. An update finds them by the old name and renames them, so an
# upgrade never leaves a second workflow behind (two active workflows on one webhook path would collide in n8n).
# The Turkish letters are written as escapes: this file stays free of them (bridge/tests/test_bridge_i18n.py).
LEGACY_NAMES = {
    "Orkestra \u00b7 Start a job via webhook": ("Orkestra \u00b7 Webhook ile i\u015f ba\u015flat",),
    "Orkestra \u00b7 Notifications (callback)": ("Orkestra \u00b7 Bildirimler (callback)",),
}


def resolve_api_key() -> str:
    key = (os.environ.get("N8N_API_KEY") or "").strip()
    if key:
        return key
    state = (os.environ.get("N8N_STATE") or "").strip()
    if not state:
        sys.exit(t("bridge.n8n.need_key"))
    path = Path(state).expanduser()
    try:
        return json.loads(path.read_text(encoding="utf-8"))["apiKey"]
    except FileNotFoundError:
        sys.exit(t("bridge.n8n.missing_file", path=path))
    except (KeyError, json.JSONDecodeError, TypeError) as exc:
        sys.exit(t("bridge.n8n.bad_state", path=path, error=exc))


def api(path, method="GET", body=None, key=""):
    req = urllib.request.Request(
        N8N_URL + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-N8N-API-KEY": key},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


def ensure_webhook_credential(key: str, cfg: dict) -> str:
    """Header-auth credential protecting the n8n webhook (n8n stores the secret encrypted)."""
    secret = cfg.get("n8n_webhook_key")
    if not secret:
        import secrets as _s
        secret = _s.token_urlsafe(24)
        cfg["n8n_webhook_key"] = secret
        tmp = BRIDGE_CFG.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, BRIDGE_CFG)
    cred = api(
        "/api/v1/credentials",
        "POST",
        {
            "name": "Orkestra webhook key",
            "type": "httpHeaderAuth",
            "data": {"name": "X-Orkestra-Key", "value": secret},
        },
        key,
    )
    return cred["id"]


def find_existing(existing, name):
    """The id of the workflow called `name` (or by an older name of it), else None."""
    for candidate in (name,) + tuple(LEGACY_NAMES.get(name, ())):
        if candidate in existing:
            return existing[candidate]
    return None


def main() -> int:
    key = resolve_api_key()
    cfg = json.loads(BRIDGE_CFG.read_text(encoding="utf-8"))
    token = cfg["token"]
    cred_id = ensure_webhook_credential(key, cfg)
    existing = {w["name"]: w["id"] for w in api("/api/v1/workflows?limit=250", key=key).get("data", [])}
    for fname, activate in (("orkestra-webhook-is-baslat.json", True), ("orkestra-bildirimler.json", False)):
        raw = (HERE / "n8n" / fname).read_text(encoding="utf-8")
        wf = json.loads(raw.replace("__ORKESTRA_TOKEN__", token).replace("__CRED_ID__", cred_id))
        body = {k: wf[k] for k in ("name", "nodes", "connections", "settings")}
        wid = find_existing(existing, wf["name"])
        if wid:
            api(f"/api/v1/workflows/{wid}", "PUT", body, key)
            print(t("bridge.n8n.updated", name=wf["name"], id=wid))
        else:
            wid = api("/api/v1/workflows", "POST", body, key)["id"]
            print(t("bridge.n8n.created", name=wf["name"], id=wid))
        if activate:
            try:
                api(f"/api/v1/workflows/{wid}/activate", "POST", None, key)
                print(t("bridge.n8n.active", url=N8N_URL, config=BRIDGE_CFG))
            except urllib.error.HTTPError as exc:
                print(t("bridge.n8n.activate_failed", code=exc.code))
    print(t("bridge.n8n.callback_url", url=N8N_URL))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as exc:
        sys.exit(t("bridge.n8n.missing_file", path=exc.filename))
    except urllib.error.URLError as exc:
        sys.exit(t("bridge.n8n.unreachable", url=N8N_URL, error=exc))
