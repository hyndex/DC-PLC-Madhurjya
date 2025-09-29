#!/usr/bin/env python3
"""Parse EVSE logs and produce a quick verification summary.

Extracts:
- CurrentDemand latency (if 'CurrentDemandReq received'/'Sent CurrentDemandRes' present)
- CP state stability (counts of transitions, time in C/D, any emergency E/F)
- Power mismatch warnings (transient/persistent)
- cd_set snapshots (requested vs commanded V/I)

Usage:
  python scripts/verify_session_logs.py /path/to/evse.log
"""
from __future__ import annotations

import re
import sys
import json
import statistics
from datetime import datetime


def _try_ts(line: str) -> float | None:
    # Accept 'YYYY-mm-dd HH:MM:SS,ms' prefix or epoch or none
    try:
        # e.g., 2025-09-16 23:06:37,355 INFO ...
        ts = line.split(" ", 2)[:2]
        if len(ts) >= 2 and "," in ts[1]:
            dt = datetime.strptime(" ".join(ts), "%Y-%m-%d %H:%M:%S,%f")
            return dt.timestamp()
    except Exception:
        pass
    return None


def main(path: str) -> int:
    lat_pairs: list[tuple[float, float]] = []
    cd_ticks: list[dict] = []
    last_cd_ts: float | None = None
    cd_periods_ms: list[float] = []
    last_req_ts: float | None = None
    p95 = p999 = None
    cp_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}
    cp_transitions = 0
    last_cp = None
    mismatch_transient = 0
    mismatch_persistent = 0
    cd_set_snapshots: list[dict] = []

    req_pat = re.compile(r"CurrentDemandReq received", re.IGNORECASE)
    res_pat = re.compile(r"Sent CurrentDemandRes", re.IGNORECASE)
    cp_state_pat = re.compile(r"CP state\b|CP emergency state", re.IGNORECASE)
    cp_char_pat = re.compile(r"\bstate['\"]?:\s*['\"]?([A-Fa-f])")
    mismatch_trans_pat = re.compile(r"Transient power mismatch beyond tolerance", re.IGNORECASE)
    mismatch_pers_pat = re.compile(r"Persistent power mismatch beyond tolerance", re.IGNORECASE)
    cd_set_pat = re.compile(r"cd_set.*\{.*\}")
    cd_tick_pat = re.compile(r"cd_tick.*\{.*\}")

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            ts = _try_ts(line)
            # Latency (if present)
            if req_pat.search(line):
                if ts is not None:
                    last_req_ts = ts
            elif res_pat.search(line):
                if ts is not None and last_req_ts is not None:
                    lat_pairs.append((last_req_ts, ts))
                    last_req_ts = None

            # CP state transitions
            if cp_state_pat.search(line):
                m = cp_char_pat.search(line)
                if m:
                    st = m.group(1).upper()
                    if st in cp_counts:
                        cp_counts[st] += 1
                        if last_cp is None:
                            last_cp = st
                        elif last_cp != st:
                            cp_transitions += 1
                            last_cp = st

            # Mismatch
            if mismatch_trans_pat.search(line):
                mismatch_transient += 1
            if mismatch_pers_pat.search(line):
                mismatch_persistent += 1

            # cd_set snapshot
            if "cd_set" in line:
                # Expect JSON-ish dict in extra
                try:
                    jstart = line.index("{")
                    jobj = json.loads(line[jstart:].strip().rstrip())
                    cd_set_snapshots.append(jobj)
                except Exception:
                    pass
            # cd_tick telemetry
            if "cd_tick" in line:
                try:
                    jstart = line.index("{")
                    jobj = json.loads(line[jstart:].strip().rstrip())
                    cd_ticks.append(jobj)
                    tsj = jobj.get("ts")
                    try:
                        ts = float(tsj)
                    except Exception:
                        ts = 0.0
                    if ts and last_cd_ts and ts >= last_cd_ts:
                        cd_periods_ms.append((ts - last_cd_ts) * 1000.0)
                    last_cd_ts = ts
                except Exception:
                    pass

    # Compute latency stats if any
    if lat_pairs:
        lats = [(b - a) * 1000.0 for a, b in lat_pairs if b >= a]
        if lats:
            p95 = statistics.quantiles(lats, n=100)[94]
            # p99.9 approx by sorting if enough samples
            lats_sorted = sorted(lats)
            idx = min(len(lats_sorted) - 1, int(round(0.999 * (len(lats_sorted) - 1))))
            p999 = lats_sorted[idx]

    # Derive simple period stats
    cd_period = {}
    if cd_periods_ms:
        try:
            cd_sorted = sorted(cd_periods_ms)
            p95_idx = max(0, int(0.95 * (len(cd_sorted) - 1)))
            cd_period = {
                "samples": len(cd_sorted),
                "avg_ms": round(sum(cd_sorted) / len(cd_sorted), 1),
                "p95_ms": round(cd_sorted[p95_idx], 1),
            }
        except Exception:
            cd_period = {"samples": len(cd_periods_ms)}

    summary = {
        "latency_ms": {
            "samples": len(lat_pairs),
            "p95": round(p95, 1) if p95 is not None else None,
            "p999": round(p999, 1) if p999 is not None else None,
        },
        "cp_counts": cp_counts,
        "cp_transitions": cp_transitions,
        "mismatch": {
            "transient": mismatch_transient,
            "persistent": mismatch_persistent,
        },
        "cd_set_samples": len(cd_set_snapshots),
        "cd_tick_samples": len(cd_ticks),
        "cd_period_ms": cd_period,
    }
    print(json.dumps(summary, indent=2))
    # Fail if persistent mismatch found
    return 1 if mismatch_persistent > 0 else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: verify_session_logs.py /path/to/evse.log", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
