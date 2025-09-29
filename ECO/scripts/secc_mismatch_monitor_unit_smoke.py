#!/usr/bin/env python3
"""Smoke test for PowerMismatchMonitor (no network).

Validates precharge tolerance/timeout and steady mismatch warn/abort transitions.
Prints PASS/FAIL and exits with code 0/1.
"""

import sys

from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL_ISO15118_ROOT = HERE / "src" / "iso15118"
if (LOCAL_ISO15118_ROOT / "iso15118" / "__init__.py").is_file():
    p = str(LOCAL_ISO15118_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

from iso15118.secc.mismatch_monitor import PowerMismatchMonitor


def run() -> bool:
    ok = True
    # Precharge ok
    mon = PowerMismatchMonitor(precharge_tol_v=20.0, precharge_timeout_s=5.0)
    mon.begin_precharge(400.0, now_mono=100.0)
    ok &= mon.check_precharge(385.0, measured_current_a=1.0, now_mono=100.1)[0]

    # Precharge timeout
    mon = PowerMismatchMonitor(precharge_tol_v=10.0, precharge_timeout_s=0.5)
    mon.begin_precharge(400.0, now_mono=200.0)
    pre_ok, _ = mon.check_precharge(360.0, measured_current_a=0.1, now_mono=200.6)
    ok &= (not pre_ok)

    # Steady: ok -> warn -> abort
    mon = PowerMismatchMonitor(
        steady_v_tol_frac=0.05,
        steady_i_tol_frac=0.05,
        mismatch_grace_s=0.2,
        mismatch_abort_s=0.5,
        min_current_for_check_a=2.0,
    )
    r1 = mon.check_steady(400.0, 100.0, 400.0, 100.0, now_mono=1000.0)
    r2 = mon.check_steady(410.0, 104.0, 400.0, 100.0, now_mono=1000.1)
    r3 = mon.check_steady(500.0, 30.0, 400.0, 100.0, now_mono=1000.15)
    r4 = mon.check_steady(500.0, 30.0, 400.0, 100.0, now_mono=1000.35)
    r5 = mon.check_steady(500.0, 30.0, 400.0, 100.0, now_mono=1000.7)
    ok &= r1.ok and r1.action == "continue"
    ok &= r2.ok and r2.action == "continue"
    ok &= r3.ok and r3.action == "continue"
    ok &= r4.ok and r4.action == "warn"
    ok &= (not r5.ok) and r5.action == "abort"
    return ok


if __name__ == "__main__":
    success = run()
    print("result:", "PASS" if success else "FAIL")
    raise SystemExit(0 if success else 1)

