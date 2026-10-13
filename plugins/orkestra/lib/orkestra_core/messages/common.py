"""common: strings shared by several areas (English + Turkish). Keys are `common.<name>`; both dicts must hold the same
keys and {fields}."""
from __future__ import annotations

from typing import Dict

EN: Dict[str, str] = {
    "common.ago": "{dur} ago",
    "common.lock_busy": "{name} is busy: waited {waited} s, held by {holder}. Wait for it to finish, or stop that "
                        "process if it is stuck.",
    "common.lock_holder": "pid {pid} ({cmd}) for {held} s",
    "common.lock_holder_unknown": "another process",
}

TR: Dict[str, str] = {
    "common.ago": "{dur} önce",
    "common.lock_busy": "{name} meşgul: {waited} sn beklendi, tutan: {holder}. Bitmesini bekleyin; takıldıysa o "
                        "işlemi durdurun.",
    "common.lock_holder": "pid {pid} ({cmd}), {held} sn",
    "common.lock_holder_unknown": "başka bir işlem",
}
