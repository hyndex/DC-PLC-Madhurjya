#!/usr/bin/env python3
"""
ESP + SLAC smoke test against the FastAPI server.

Steps:
- Ping the ESP via /esp/ping
- Wait for CP state B/C via /vehicle/live
- Start SLAC matching via /slac/start_matching (sim)
- Time until MATCHED (or timeout); on timeout, try /esp/restart_slac and retry once
- Snapshot /vehicle/live (includes CP/SLAC/ISO and BMS data)

Usage:
  python scripts/esp_slac_smoke.py --base http://localhost:8000 --timeout 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict

try:
    import httpx  # type: ignore
except Exception:
    httpx = None
    import urllib.request as urlreq


def _get(base: str, path: str) -> Dict[str, Any]:
    url = base.rstrip("/") + path
    if httpx:
        r = httpx.get(url, timeout=5.0)
        r.raise_for_status()
        return r.json()
    with urlreq.urlopen(url, timeout=5.0) as r:  # type: ignore
        return json.loads(r.read().decode())


def _post(base: str, path: str, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    url = base.rstrip("/") + path
    payload = json.dumps(data or {}).encode()
    headers = {"Content-Type": "application/json"}
    if httpx:
        r = httpx.post(url, content=payload, headers=headers, timeout=5.0)
        r.raise_for_status()
        return r.json()
    req = urlreq.Request(url, data=payload, headers=headers, method="POST")  # type: ignore
    with urlreq.urlopen(req, timeout=5.0) as r:  # type: ignore
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--timeout", type=int, default=30, help="overall timeout (s)")
    args = ap.parse_args()
    t0 = time.time()

    print(f"[1/5] Pinging ESP at {args.base} ...", flush=True)
    try:
        pong = _post(args.base, "/esp/ping")
        print("    pong:", pong.get("pong"))
    except Exception as e:
        print("    ERROR: /esp/ping failed:", e)
        return 2

    print("[2/5] Waiting for CP state B/C ...", flush=True)
    deadline = time.time() + args.timeout
    cp_state = None
    while time.time() < deadline:
        try:
            live = _get(args.base, "/vehicle/live")
            cp = (live.get("cp") or {})
            cp_state = cp.get("state")
            if cp_state in ("B", "C", "D"):
                print("    CP:", cp)
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        print("    TIMEOUT waiting for CP B/C/D")
        return 3

    print("[3/5] Starting SLAC matching (sim API) ...")
    try:
        _post(args.base, "/slac/start_matching")
    except Exception as e:
        print("    WARN: /slac/start_matching failed:", e)

    print("[4/5] Waiting for SLAC MATCHED ...")
    start = time.time()
    matched = False
    while time.time() < start + args.timeout:
        try:
            live = _get(args.base, "/vehicle/live")
            slac = live.get("slac") or {}
            if (slac.get("state") or "").upper() == "MATCHED":
                matched = True
                print("    SLAC matched in", round(time.time() - start, 2), "s")
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not matched:
        print("    SLAC not matched in time; sending ESP restart hint and retrying once ...")
        try:
            _post(args.base, "/esp/restart_slac", {"reset_ms": 400})
        except Exception as e:
            print("    WARN: /esp/restart_slac failed:", e)
        start = time.time()
        while time.time() < start + args.timeout:
            try:
                live = _get(args.base, "/vehicle/live")
                slac = live.get("slac") or {}
                if (slac.get("state") or "").upper() == "MATCHED":
                    matched = True
                    print("    SLAC matched in", round(time.time() - start, 2), "s (after restart)")
                    break
            except Exception:
                pass
            time.sleep(0.5)

    print("[5/5] Snapshot /vehicle/live ...")
    try:
        live = _get(args.base, "/vehicle/live")
        print(json.dumps(live, indent=2))
    except Exception as e:
        print("    ERROR: /vehicle/live failed:", e)

    print("Done in", round(time.time() - t0, 2), "s")
    return 0 if matched else 4


if __name__ == "__main__":
    sys.exit(main())

