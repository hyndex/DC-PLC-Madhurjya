#!/usr/bin/env python3
"""
Tail the EVSE JSON log and report HLC phase progress for DC charging.

Watches for ISO 15118 states typically seen in DC flows and writes a
summary to /tmp/hlc_progress.json as soon as they appear.

Milestones tracked:
  - SessionSetup
  - ServiceDiscovery
  - Authorization
  - ChargeParameterDiscovery

Also prints progress to stdout as they occur. Exits when all milestones
are observed. Safe to run before HLC starts; it will wait.

Usage:
  python scripts/wait_hlc_phases.py --log /tmp/evse_run.jsonl --timeout 0
  (timeout=0 means wait forever)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List


MILESTONES = [
    "SessionSetup",
    "ServiceDiscovery",
    "Authorization",
    "ChargeParameterDiscovery",
]


def update_progress(prog: Dict[str, str]) -> None:
    Path("/tmp/hlc_progress.json").write_text(json.dumps(prog, indent=2))


def find_in_lines(lines: List[str], prog: Dict[str, str]) -> bool:
    changed = False
    for line in lines:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        # We emit structured HLC state logs from iso15118_hal_controller using logger name 'hlc'
        if obj.get("name") != "hlc":
            continue
        state = obj.get("iso_state") or obj.get("state") or ""
        if not isinstance(state, str):
            continue
        for m in MILESTONES:
            if m in state and m not in prog:
                prog[m] = obj.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                print(json.dumps({"milestone": m, "ts": prog[m], "iso_state": state}))
                changed = True
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/tmp/evse_run.jsonl")
    ap.add_argument("--timeout", type=float, default=0.0)
    args = ap.parse_args()

    logp = Path(args.log)
    # Wait for log file to appear
    t0 = time.time()
    while not logp.exists():
        if args.timeout and (time.time() - t0) > args.timeout:
            print("[wait_hlc] Timeout waiting for log file", file=sys.stderr)
            return 1
        time.sleep(0.2)

    prog: Dict[str, str] = {}
    # Seed from existing content
    try:
        lines = logp.read_text().splitlines()
    except Exception:
        lines = []
    if find_in_lines(lines, prog):
        update_progress(prog)
    if all(m in prog for m in MILESTONES):
        return 0

    with logp.open("r", encoding="utf-8") as f:
        f.seek(0, 2)  # seek end
        deadline = (time.time() + args.timeout) if args.timeout else None
        while True:
            line = f.readline()
            if not line:
                if deadline and time.time() > deadline:
                    return 2
                time.sleep(0.2)
                continue
            if find_in_lines([line], prog):
                update_progress(prog)
                if all(m in prog for m in MILESTONES):
                    return 0


if __name__ == "__main__":
    raise SystemExit(main())

