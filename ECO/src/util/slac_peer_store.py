from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional


def _default_path() -> str:
    # Prefer runtime directory if available
    for p in ("/run/evse", "/var/run/evse", "/tmp"):
        try:
            if os.path.isdir(p) or (p.endswith("evse") and os.access(os.path.dirname(p), os.W_OK)):
                return os.path.join(p, "slac_peer.json") if p != "/tmp" else "/tmp/evse_slac_peer.json"
        except Exception:
            pass
    return "/tmp/evse_slac_peer.json"


def write_peer(ev_mac: Optional[str], nid: Optional[str] = None, run_id: Optional[str] = None, path: Optional[str] = None) -> str:
    data = {
        "ev_mac": ev_mac,
        "nid": nid,
        "run_id": run_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    dst = path or _default_path()
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    except Exception:
        pass
    tmp = f"{dst}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, dst)
    return dst


def read_peer(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    src = path or _default_path()
    try:
        with open(src, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

