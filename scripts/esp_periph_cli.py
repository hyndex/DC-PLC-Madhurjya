#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from typing import Any, Dict

import serial  # type: ignore


def send_req(ser: serial.Serial, method: str, params: Dict[str, Any] | None = None, timeout: float = 1.5) -> Dict[str, Any]:
    rid = str(uuid.uuid4())
    obj = {"type": "req", "id": rid, "method": method, "params": params or {}}
    ser.write((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))
    deadline = time.time() + max(0.1, timeout)
    while time.time() < deadline:
        line = ser.readline()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8").strip())
        except Exception:
            continue
        if isinstance(msg, dict) and msg.get("type") == "res" and str(msg.get("id")) == rid:
            if "error" in msg and msg["error"]:
                raise RuntimeError(str(msg["error"]))
            return dict(msg.get("result", {}))
    raise TimeoutError(f"timeout waiting for {method}")


def main() -> int:
    ap = argparse.ArgumentParser(description="ESP periph JSON-RPC CLI")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")
    sub.add_parser("info")
    sub.add_parser("arm")
    cset = sub.add_parser("contactor"); cset.add_argument("on", type=int, choices=[0,1])
    sub.add_parser("discover")
    dset = sub.add_parser("set"); dset.add_argument("--v", type=float, required=True); dset.add_argument("--i", type=float, required=True)
    dena = sub.add_parser("enable"); dena.add_argument("on", type=int, choices=[0,1])
    sub.add_parser("status")
    sub.add_parser("estop")

    args = ap.parse_args()
    ser = serial.Serial(args.port, args.baud, timeout=0.3)
    try:
        if args.cmd == "ping":
            print(json.dumps(send_req(ser, "sys.ping")))
        elif args.cmd == "info":
            print(json.dumps(send_req(ser, "sys.info")))
        elif args.cmd == "arm":
            print(json.dumps(send_req(ser, "sys.arm")))
        elif args.cmd == "contactor":
            print(json.dumps(send_req(ser, "contactor.set", {"on": bool(args.on)})))
        elif args.cmd == "discover":
            print(json.dumps(send_req(ser, "dc.discover")))
        elif args.cmd == "set":
            print(json.dumps(send_req(ser, "dc.set", {"v": float(args.v), "i": float(args.i)})))
        elif args.cmd == "enable":
            print(json.dumps(send_req(ser, "dc.enable", {"on": bool(args.on)})))
        elif args.cmd == "status":
            print(json.dumps(send_req(ser, "dc.status")))
        elif args.cmd == "estop":
            print(json.dumps(send_req(ser, "dc.estop")))
        else:
            ap.error("unknown cmd")
        return 0
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
