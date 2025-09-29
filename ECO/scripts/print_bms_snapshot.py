#!/usr/bin/env python3
"""
Print the latest BMS snapshot from the EVSE JSON tee log.

Usage:
  scripts/print_bms_snapshot.py [/path/to/evse.jsonl]

Default path: /tmp/evse_e2e.jsonl

Emits a single JSON object to stdout with fields like:
  {
    "present_soc": 83,
    "target_voltage": 365.8,
    "target_current": 26.5,
    "max_current_limit": 65.0,
    "max_voltage": 375.0,
    "evcc_id": "...",
    "_evse": { ... }
  }

Returns an empty object ({}) if none found.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/evse_e2e.jsonl"
    try:
        with open(path, "r") as f:
            last = None
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                # Look for top-level structured extras from the HLC logger
                if "bms" in obj and isinstance(obj["bms"], dict):
                    last = obj["bms"]
                    evse = obj.get("evse")
                    if isinstance(evse, dict):
                        last["_evse"] = evse
                # Some log sinks may nest structured data under 'extra'
                elif "extra" in obj and isinstance(obj["extra"], dict):
                    ex = obj["extra"]
                    if "bms" in ex and isinstance(ex["bms"], dict):
                        last = ex["bms"]
                        evse = ex.get("evse")
                        if isinstance(evse, dict):
                            last["_evse"] = evse
    except FileNotFoundError:
        last = None
    print(json.dumps(last or {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

