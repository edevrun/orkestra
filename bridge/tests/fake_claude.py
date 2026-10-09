#!/usr/bin/env python3
"""Stand-in for `claude -p` used by the bridge tests: drives the real orkestra CLI like a headless run would."""
import json
import os
import subprocess
import sys
from pathlib import Path

ORK = Path(__file__).resolve().parents[2] / "plugins" / "orkestra" / "bin" / "orkestra"
args = sys.argv[1:]
prompt = sys.stdin.read()
sid = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
assert os.environ.get("ORKESTRA_HEADLESS") == "1", "bridge must run headless"


def ork(*a):
    return subprocess.run([sys.executable, str(ORK), *a, "-p", os.getcwd()], capture_output=True, text=True)


if prompt.startswith("/orkestra:run"):
    ork("start", "--title", "fake", "--request", prompt[len("/orkestra:run "):], "--size", "S")
    ork("await", "Varsayılan öncelik ne olsun?")
    result = "1. Varsayılan öncelik ne olsun? (a) medium (Önerilen)"
elif prompt.strip() == "engelle":
    ork("block", "kullanıcı test etti")
    result = "Engellendi: test"
elif prompt.strip() == "takıl":            # the process ends while the run is still active (a stall)
    ork("unblock")
    result = "yarıda kaldım"
elif prompt.strip() == "hep takıl":        # keeps stalling even when resumed
    ork("unblock")
    (Path(os.getcwd()) / ".always-stall").write_text("1")
    result = "yarıda kaldım"
elif prompt.startswith("/orkestra:resume"):
    if (Path(os.getcwd()) / ".always-stall").exists():
        result = "yine yarıda kaldım"
    else:
        ork("await", "Devam sorusu?")
        result = "kaldığım yerden devam ettim; bir sorum var"
else:
    result = f"devam ediyorum: {prompt}"
print(json.dumps({"type": "result", "result": result, "session_id": sid}))
