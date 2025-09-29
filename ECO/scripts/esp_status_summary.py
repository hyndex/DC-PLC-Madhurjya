#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Dict, List


def summarize(obj: Dict[str, Any]) -> str:
    enabled = bool(obj.get("enabled", False))
    v_set = float(obj.get("v_set", 0.0) or 0.0)
    i_set = float(obj.get("i_set", 0.0) or 0.0)
    tele = obj.get("tele") or []
    if not isinstance(tele, list):
        tele = []
    mv: List[float] = []
    ma: List[float] = []
    faults: List[str] = []
    for m in tele:
        try:
            v_mv = float(m.get("v_mv", 0.0) or 0.0)
            i_ma = float(m.get("i_ma", 0.0) or 0.0)
            mv.append(v_mv)
            ma.append(i_ma)
            st = int(m.get("st", 0) or 0)
            if st:
                # list set bit indices
                bits = [str(i) for i in range(32) if (st >> i) & 1]
                faults.append(f"addr={m.get('addr')} st=0x{st:08X} bits=[{','.join(bits)}]")
        except Exception:
            continue
    v_avg = (sum(mv) / len(mv) / 1000.0) if mv else 0.0
    i_sum = (sum(ma) / 1000.0) if ma else 0.0
    parts = [
        f"enabled={1 if enabled else 0}",
        f"Vset={v_set:.1f}V",
        f"Iset={i_set:.1f}A",
        f"Vavg={v_avg:.1f}V",
        f"Isum={i_sum:.1f}A",
        f"mods={len(tele)}",
    ]
    if faults:
        parts.append("faults=" + "; ".join(faults))
    return " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize dc.status JSON")
    ap.add_argument("--json", help="JSON input; otherwise read from stdin")
    args = ap.parse_args()
    try:
        s = args.json if args.json else sys.stdin.read()
        obj = json.loads(s)
        print(summarize(obj))
        return 0
    except Exception as e:
        print(f"parse_error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

