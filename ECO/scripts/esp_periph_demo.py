#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import sys
from pathlib import Path

# Ensure repo root is on sys.path so 'src' package is importable
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evse_hal.esp_periph_client import EspPeriphClient


def main() -> None:
    p = argparse.ArgumentParser(description="ESP32-S3 peripheral coprocessor demo")
    p.add_argument("--port", default=None, help="Serial port (default from ESP_PERIPH_PORT or /dev/ttyUSB0)")
    args = p.parse_args()

    c = EspPeriphClient(port=args.port)
    c.connect()

    def on_evt(name: str, payload):
        print("[EVT]", name, json.dumps(payload))

    c.on_event(on_evt)

    print("Info:", c.sys_info())
    print("Ping:", c.sys_ping())
    print("Mode:", c.sys_set_mode("sim"))

    print("Arm:", c.sys_arm())
    print("Contactor check:", c.contactor_check())

    print("ON:", c.contactor_set(True))
    time.sleep(0.2)
    print("Check:", c.contactor_check())

    print("Temps:", c.temps_read())
    m = c.meter_read()
    print("Meter:", m)

    # Stream demo
    try:
        c.send_req("meter.stream_start", {"period_ms": 1000}, timeout=0.5)
    except Exception:
        pass
    time.sleep(3.2)
    try:
        c.send_req("meter.stream_stop", {}, timeout=0.5)
    except Exception:
        pass

    print("Arm:", c.sys_arm())
    print("OFF:", c.contactor_set(False))
    print("Done")

    # CP helper diagnostics (same UART)
    try:
        print("CP ping:", c.cp_ping())
        c.cp_set_mode("manual")
        c.cp_set_pwm(100, enable=True)
        time.sleep(0.2)
        st = c.cp_get_status()
        print("CP status:", {"mode": getattr(st, "mode", None), "duty": getattr(getattr(st, "pwm", None), "duty", None)})
        c.cp_set_mode("dc")
    except Exception as e:
        print("[CP DIAG ERR]", e)


if __name__ == "__main__":
    main()
