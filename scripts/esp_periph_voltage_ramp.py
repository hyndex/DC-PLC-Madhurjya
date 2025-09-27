#!/usr/bin/env python3
"""
ESP Periph DC voltage ramp test (bench/field)

Drives the rectifier via the ESP32-S3 peripheral controller from START to END
in STEPs of volts, holding HOLD seconds at each step. Then ramps down back to
START and finally turns output off.

Also prints meter readings each step and warns if measured voltage deviates
from target beyond tolerance.

Usage examples:
  python scripts/esp_periph_voltage_ramp.py --port /dev/ttyACM0 \
    --start 200 --end 1000 --step 50 --hold 5 --amps 5

Environment fallback for --port:
  ESP_PERIPH_PORT or ESP_CP_PORT
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path


def _ensure_paths() -> None:
    # Add repository's src/ to sys.path so we can `import evse_hal.*`
    src_dir = Path(__file__).resolve().parents[1] / "src"
    p = str(src_dir)
    if p not in sys.path:
        sys.path.insert(0, p)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=None, help="ESP periph serial (e.g., /dev/ttyACM0)")
    ap.add_argument("--start", type=float, default=200.0, help="Start voltage (V)")
    ap.add_argument("--end", type=float, default=1000.0, help="End voltage (V)")
    ap.add_argument("--step", type=float, default=50.0, help="Step size (V)")
    ap.add_argument("--hold", type=float, default=5.0, help="Hold time at step (s)")
    ap.add_argument("--amps", type=float, default=5.0, help="Current limit (A)")
    ap.add_argument("--cycles", type=int, default=1, help="Number of up/down cycles")
    ap.add_argument("--tol-v", type=float, default=10.0, help="Voltage tolerance for warning (V)")
    ap.add_argument("--force-dc", action="store_true", help="Force CP to DC mode via ESP")
    ap.add_argument("--periph-mode", choices=["hw","sim"], default="sim", help="Set ESP peripheral mode (sim bypasses AUX)")
    ap.add_argument("--cfg-v-min", type=float, default=200.0, help="Periph config: min voltage (V)")
    ap.add_argument("--cfg-v-max", type=float, default=1000.0, help="Periph config: max voltage (V)")
    ap.add_argument("--cfg-p-kw", type=float, default=30.0, help="Periph config: power cap (kW)")
    ap.add_argument("--cfg-i-max", type=float, default=120.0, help="Periph config: hard current limit (A)")
    return ap.parse_args()


def main() -> int:
    _ensure_paths()
    # Lazy import after sys.path setup
    from evse_hal.esp_periph_client import EspPeriphClient  # type: ignore
    import os

    args = parse_args()

    port = (
        args.port
        or os.environ.get("ESP_PERIPH_PORT")
        or os.environ.get("ESP_CP_PORT")
        or "/dev/ttyACM0"
    )

    stop = {"flag": False}

    def _sig(_signo, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    c = EspPeriphClient(port=port)
    print(f"[ramp] Opening ESP periph on {port} ...", flush=True)
    c.connect()

    try:
        info = c.sys_info(timeout=1.0)
        print("[ramp] sys.info:", info)
    except Exception as e:
        print("[ramp] sys.info failed:", e)

    # Set peripheral mode (sim bypasses AUX gating on contactor)
    try:
        mode_res = c.send_req("sys.set_mode", {"mode": args.periph_mode})
        print("[ramp] sys.set_mode:", mode_res)
    except Exception as e:
        print("[ramp] sys.set_mode failed:", e)

    if args.force_dc:
        try:
            c.cp_set_mode("dc")
            print("[ramp] Forced CP mode to 'dc'")
        except Exception as e:
            print("[ramp] cp_set_mode(dc) failed:", e)

    # Configure periph limits (optional but recommended)
    try:
        cfg = c.send_req("dc.cfg", {"v_min": args.cfg_v_min, "v_max": args.cfg_v_max, "p_kw": args.cfg_p_kw, "i_max": args.cfg_i_max})
        print("[ramp] dc.cfg:", cfg)
    except Exception as e:
        print("[ramp] dc.cfg failed:", e)

    # Arm before enabling DC (firmware policy may require this)
    try:
        c.sys_arm()
    except Exception:
        pass

    # Helper to sample meter
    def read_meter():
        try:
            m = c.meter_read()
            return float(m.voltage_v), float(m.current_a), float(m.power_kw), float(m.energy_kwh)
        except Exception:
            return 0.0, 0.0, 0.0, 0.0

    # Quick comm check
    try:
        res = c.send_req("dc.comm.check", {"timeout_ms": 1200})
        print("[ramp] dc.comm.check:", res)
    except Exception as e:
        print("[ramp] dc.comm.check failed:", e)
    # CAN stats for diagnostics
    try:
        stats = c.send_req("can.stats", {})
        print("[ramp] can.stats:", stats)
    except Exception as e:
        print("[ramp] can.stats failed:", e)

    # Enable DC (ignore CP gating to allow bench energize)
    print("[ramp] Enabling DC output ...")
    try:
        # Make sure ignore_cp=true
        try:
            c.send_req("dc.cfg", {"ignore_cp": True})
        except Exception:
            pass
        c.dc_enable(True)
    except Exception as e:
        print("[ramp] dc_enable(True) failed:", e)

    # Command a safe initial setpoint
    try:
        c.dc_set(volts=args.start, amps=args.amps)
    except Exception as e:
        print("[ramp] dc_set initial failed:", e)

    # Build up/down sequence
    start_v = float(args.start)
    end_v = float(args.end)
    step_v = float(args.step) if args.step > 0 else 50.0
    hold_s = max(0.1, float(args.hold))

    def frange(a: float, b: float, s: float):
        x = a
        if s == 0:
            yield x
            return
        if a <= b:
            while x <= b + 1e-6:
                yield round(x, 3)
                x += s
        else:
            while x >= b - 1e-6:
                yield round(x, 3)
                x -= abs(s)

    try:
        for cyc in range(int(args.cycles)):
            if stop["flag"]:
                break
            print(f"[ramp] Cycle {cyc+1}/{args.cycles} ascending ...")
            for v in frange(start_v, end_v, step_v):
                if stop["flag"]:
                    break
                try:
                    c.dc_set(volts=v, amps=args.amps)
                except Exception as e:
                    print(f"[ramp] dc_set(v={v}, i={args.amps}) failed:", e)
                t0 = time.time()
                while time.time() - t0 < hold_s:
                    mv, mi, mp_kw, me_kwh = read_meter()
                    warn = ""
                    if abs(mv - v) > args.tol_v:
                        warn = f" (WARN |ΔV|={abs(mv-v):.1f}V > {args.tol_v}V)"
                    print(f"  Vt={v:.1f}V It={args.amps:.1f}A | Vm={mv:.1f}V Im={mi:.1f}A P={mp_kw:.2f}kW E={me_kwh:.3f}kWh{warn}")
                    time.sleep(min(1.0, max(0.2, hold_s/2)))

            if stop["flag"]:
                break
            print(f"[ramp] Cycle {cyc+1}/{args.cycles} descending ...")
            for v in frange(end_v, start_v, step_v):
                if stop["flag"]:
                    break
                try:
                    c.dc_set(volts=v, amps=args.amps)
                except Exception as e:
                    print(f"[ramp] dc_set(v={v}, i={args.amps}) failed:", e)
                t0 = time.time()
                while time.time() - t0 < hold_s:
                    mv, mi, mp_kw, me_kwh = read_meter()
                    warn = ""
                    if abs(mv - v) > args.tol_v:
                        warn = f" (WARN |ΔV|={abs(mv-v):.1f}V > {args.tol_v}V)"
                    print(f"  Vt={v:.1f}V It={args.amps:.1f}A | Vm={mv:.1f}V Im={mi:.1f}A P={mp_kw:.2f}kW E={me_kwh:.3f}kWh{warn}")
                    time.sleep(min(1.0, max(0.2, hold_s/2)))

    finally:
        # Turn output off and disable
        try:
            print("[ramp] Setting voltage to 0 V ...")
            c.dc_set(volts=0.0, amps=0.0)
        except Exception:
            pass
        time.sleep(0.5)
        try:
            print("[ramp] Disabling DC output ...")
            c.dc_enable(False)
        except Exception:
            pass
        try:
            c.close()
        except Exception:
            pass
        print("[ramp] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
