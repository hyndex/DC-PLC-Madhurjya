#!/usr/bin/env python3
"""
Tail a JSON log (EVSE tee) and emit the first BMS/Precharge snapshot.

Default log: /tmp/evse_run.jsonl
Writes a copy to /tmp/evse_bms_snapshot.json when found.

Usage:
  python scripts/wait_bms.py [--log /tmp/evse_run.jsonl] [--timeout 0]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def find_bms_in_lines(lines: list[str]):
    for line in lines:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("name") == "hlc" and isinstance(obj.get("bms"), dict):
            return {
                "ts": obj.get("ts"),
                "iso_state": obj.get("iso_state"),
                "bms": obj.get("bms"),
                "evse": obj.get("evse"),
            }
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/tmp/evse_run.jsonl")
    ap.add_argument("--timeout", type=float, default=0.0, help="0 = wait forever")
    args = ap.parse_args()

    logp = Path(args.log)
    # Wait for the file to appear
    t0 = time.time()
    while not logp.exists():
        if args.timeout and (time.time() - t0) > args.timeout:
            print("[wait_bms] Timeout waiting for log file", file=sys.stderr)
            return 1
        time.sleep(0.2)

    # Read existing content for any prior snapshot
    try:
        lines = logp.read_text().splitlines()
    except Exception:
        lines = []
    snap = find_bms_in_lines(reversed(lines)) if lines else None
    if snap:
        print(json.dumps(snap))
        Path("/tmp/evse_bms_snapshot.json").write_text(json.dumps(snap))
        return 0

    # Tail for new lines until we find a snapshot
    with logp.open("r", encoding="utf-8") as f:
        # Seek to end
        f.seek(0, 2)
        deadline = (time.time() + args.timeout) if args.timeout else None
        while True:
            line = f.readline()
            if not line:
                if deadline and time.time() > deadline:
                    print("[wait_bms] Timeout with no BMS snapshot", file=sys.stderr)
                    return 2
                time.sleep(0.2)
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("name") == "hlc" and isinstance(obj.get("bms"), dict):
                snap = {
                    "ts": obj.get("ts"),
                    "iso_state": obj.get("iso_state"),
                    "bms": obj.get("bms"),
                    "evse": obj.get("evse"),
                }
                print(json.dumps(snap))
                Path("/tmp/evse_bms_snapshot.json").write_text(json.dumps(snap))
                return 0


if __name__ == "__main__":
    raise SystemExit(main())

