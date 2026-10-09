#!/usr/bin/env python3
"""Import the Orkestra n8n workflows into the local n8n (uses ~/Projects/n8n/bootstrap-state.json's API key).

The bridge token is injected into the HTTP Request header. Workflow A (webhook → bridge) is activated;
workflow B (callback → Telegram) is imported inactive: pick a Telegram credential, enable the node, activate.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
N8N_URL = os.environ.get("N8N_URL", "http://localhost:5678")
STATE = Path(os.environ.get("N8N_STATE", HERE.parent.parent / "n8n" / "bootstrap-state.json"))
BRIDGE_CFG = Path(os.environ.get("ORKESTRA_HOME") or "~/.orkestra").expanduser() / "bridge.secret.json"


def api(path, method="GET", body=None, key=""):
    req = urllib.request.Request(N8N_URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-N8N-API-KEY": key})
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
        tmp.write_text(json.dumps(cfg, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, BRIDGE_CFG)
    cred = api("/api/v1/credentials", "POST", {"name": "Orkestra webhook key", "type": "httpHeaderAuth",
                                               "data": {"name": "X-Orkestra-Key", "value": secret}}, key)
    return cred["id"]


def main() -> int:
    key = json.loads(STATE.read_text())["apiKey"]
    cfg = json.loads(BRIDGE_CFG.read_text())
    token = cfg["token"]
    cred_id = ensure_webhook_credential(key, cfg)
    existing = {w["name"]: w["id"] for w in api("/api/v1/workflows?limit=250", key=key).get("data", [])}
    for fname, activate in (("orkestra-webhook-is-baslat.json", True), ("orkestra-bildirimler.json", False)):
        raw = (HERE / "n8n" / fname).read_text(encoding="utf-8")
        wf = json.loads(raw.replace("__ORKESTRA_TOKEN__", token).replace("__CRED_ID__", cred_id))
        body = {k: wf[k] for k in ("name", "nodes", "connections", "settings")}
        if wf["name"] in existing:
            wid = existing[wf["name"]]
            api(f"/api/v1/workflows/{wid}", "PUT", body, key)
            print(f"updated: {wf['name']} ({wid})")
        else:
            wid = api("/api/v1/workflows", "POST", body, key)["id"]
            print(f"created: {wf['name']} ({wid})")
        if activate:
            try:
                api(f"/api/v1/workflows/{wid}/activate", "POST", None, key)
                print(f"  active → POST {N8N_URL}/webhook/orkestra-job  header X-Orkestra-Key: <n8n_webhook_key in {BRIDGE_CFG}>")
            except urllib.error.HTTPError as exc:
                print(f"  could not activate ({exc.code}); activate it in the n8n UI")
    print(f"callback URL for the bridge config (callback_url): {N8N_URL}/webhook/orkestra-callback")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as exc:
        sys.exit(f"missing file: {exc.filename}")
    except urllib.error.URLError as exc:
        sys.exit(f"n8n not reachable at {N8N_URL}: {exc}")
