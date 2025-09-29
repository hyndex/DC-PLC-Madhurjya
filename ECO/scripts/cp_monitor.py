#!/usr/bin/env python3
"""
CP monitor and flapping detector for the ESP32-S3 CP helper.

Connects to the firmware over UART, streams status frames, and reports:
- CP state transitions with timestamps and voltages
- Effective PWM output percentage (requires recent firmware)
- Change interval stats and flapping warnings

Usage:
  ESP_CP_PORT=/dev/ttyUSB0 ./scripts/cp_monitor.py --duration 30

Options:
  --port:      Serial device (default ENV ESP_CP_PORT or /dev/serial0)
  --baud:      Baudrate (default 115200)
  --duration:  Seconds to observe before printing summary (default 0 = infinite)
  --window:    Time window (s) to judge flapping (default 10)
  --max-chg:   Max allowed changes within window before warning (default 8)
"""
from __future__ import annotations

import argparse
import os
import time
from collections import deque, defaultdict

from src.evse_hal.esp_cp_client import EspCpClient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("ESP_CP_PORT") or "/dev/serial0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--duration", type=float, default=0.0)
    ap.add_argument("--window", type=float, default=10.0)
    ap.add_argument("--max-chg", type=int, default=8)
    args = ap.parse_args()

    c = EspCpClient(port=args.port, baud=args.baud)
    c.connect()

    last = None
    t0 = time.time()
    changes: list[float] = []
    chg_in_window: deque[float] = deque()
    dwell: defaultdict[str, float] = defaultdict(float)
    last_state_ts = t0
    last_state = None

    print(f"[cpmon] Monitoring on {args.port} @ {args.baud} ...")

    try:
        while True:
            st = c.get_status(wait_s=0.5)
            now = time.time()
            if st is None:
                time.sleep(0.05)
                continue
            # Track dwell
            if last_state is None:
                last_state = st.state
                last_state_ts = now
            if st.state != last_state:
                dt = max(0.0, now - last_state_ts)
                dwell[last_state] += dt
                changes.append(now)
                chg_in_window.append(now)
                while chg_in_window and (now - chg_in_window[0]) > args.window:
                    chg_in_window.popleft()
                print(
                    f"[{now - t0:6.2f}s] CP {last_state}->{st.state} mv={st.cp_mv} robust={st.cp_mv_robust} "
                    f"mode={st.mode} pwm_out%={getattr(st.pwm, 'out', '?')}"
                )
                last_state = st.state
                last_state_ts = now
                if len(chg_in_window) > args.max_chg:
                    print(
                        f"[WARN] {len(chg_in_window)} state changes in last {args.window:.0f}s (possible flapping)"
                    )
            # Periodic live line
            if last is None or (now - last) >= 1.0:
                last = now
                print(
                    f"[{now - t0:6.2f}s] state={st.state} mv={st.cp_mv} robust={st.cp_mv_robust} "
                    f"mode={st.mode} pwm=en:{st.pwm.enabled} duty%:{st.pwm.duty} hz:{st.pwm.hz}"
                )

            if args.duration and (now - t0) >= args.duration:
                break
    except KeyboardInterrupt:
        pass

    # Summary
    total = max(0.0, time.time() - t0)
    # close last dwell
    if last_state is not None:
        dwell[last_state] += max(0.0, time.time() - last_state_ts)
    intervals = [j - i for i, j in zip(changes, changes[1:])]
    avg_int = (sum(intervals) / len(intervals)) if intervals else 0.0
    min_int = min(intervals) if intervals else 0.0
    print("\n[cpmon] Summary")
    print(f"  duration: {total:.2f}s, changes: {len(changes)}")
    print(f"  min interval: {min_int:.3f}s, avg interval: {avg_int:.3f}s")
    for st in sorted(dwell.keys()):
        print(f"  dwell[{st}]: {dwell[st]:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

