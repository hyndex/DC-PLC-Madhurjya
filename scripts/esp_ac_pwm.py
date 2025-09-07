#!/usr/bin/env python3
"""
Simple CLI to control the ESP32-S3 CP firmware for AC PWM testing via UART.

Supports:
  - Set mode (manual=AC or dc)
  - Set AC advertised current (Amps -> IEC 61851 PWM duty)
  - Set PWM duty directly (percent) in manual mode

Examples:
  python scripts/esp_ac_pwm.py --port /dev/ttyACM0 --set-mode manual
  python scripts/esp_ac_pwm.py --port /dev/ttyACM0 --set-ac-amps 16
  python scripts/esp_ac_pwm.py --port /dev/ttyACM0 --set-duty 27
"""
from __future__ import annotations

import argparse
import sys

from src.evse_hal.esp_cp_client import EspCpClient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None, help="ESP CP UART (e.g., /dev/ttyACM0)")
    ap.add_argument("--set-mode", choices=["manual", "dc"], help="Set CP mode")
    ap.add_argument("--set-ac-amps", type=float, help="Set advertised AC current (A)")
    ap.add_argument("--set-duty", type=int, help="Set PWM duty percent (manual mode)")
    args = ap.parse_args()

    c = EspCpClient(port=args.port)
    c.connect()

    try:
        if args.set_mode:
            st = c.set_mode(args.set_mode)
            print("mode:", getattr(st, "mode", None))
        if args.set_ac_amps is not None:
            # Convert amps to duty and set in manual mode
            a = max(6.0, float(args.set_ac_amps))
            duty = int(max(10, min(85, round(a / 0.6))))
            c.set_mode("manual")
            st = c.set_pwm(duty, enable=True)
            print("ac_amps:", a, "duty:", duty, "mode:", getattr(st, "mode", None))
        if args.set_duty is not None:
            duty = int(max(0, min(100, args.set_duty)))
            st = c.set_pwm(duty, enable=True)
            print("duty:", duty, "mode:", getattr(st, "mode", None))
    finally:
        try:
            c.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

