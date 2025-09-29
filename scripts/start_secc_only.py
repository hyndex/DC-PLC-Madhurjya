#!/usr/bin/env python3
"""
Start the ISO 15118 SECC directly on a given interface (no SLAC required).

This helper is intended for bench testing without a vehicle. Use it together
with scripts/send_sdp.py to discover the TCP port and scripts/evcc_min_flow.py
to drive a minimal handshake over TCP.

Usage:
  python scripts/start_secc_only.py --iface eth0 \
    --secc-config secc.env --cert-store ./pki

Environment:
  EVSE_CONTROLLER=hal|sim (default hal)
  EVSE_HAL_ADAPTER=esp-uart|esp-periph|sim (when EVSE_CONTROLLER=hal)
  EVSE_LOG_FORMAT=text|json (default text)
  EVSE_LOG_LEVEL=INFO|DEBUG|... (default INFO)
  EVSE_LOG_JSON_TEE=/tmp/evse_run.jsonl (optional structured tee)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _ensure_paths() -> None:
    here = Path(__file__).resolve().parents[1] / "src"
    p = str(here)
    if p not in sys.path:
        sys.path.insert(0, p)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iface", required=True, help="Interface for ISO15118 UDP/TCP (e.g., eth0)")
    ap.add_argument("--secc-config", default=str(Path.cwd() / "secc.env"))
    ap.add_argument("--cert-store", default=str(Path.cwd() / "pki"))
    return ap.parse_args()


async def _main() -> int:
    _ensure_paths()
    # Lazy imports after sys.path setup
    from evse_main import start_secc  # type: ignore
    from src.util.logging import setup_logging  # type: ignore

    setup_logging()
    args = parse_args()

    # Mirror CLI into env used by SECC
    if args.cert_store:
        os.environ["PKI_PATH"] = args.cert_store

    # Start SECC (runs forever)
    await start_secc(args.iface, args.secc_config, args.cert_store)
    return 0


if __name__ == "__main__":
    try:
        # Try uvloop for lower latency on Unix
        try:
            from src.util.uvloop_compat import maybe_install_uvloop  # type: ignore
        except Exception:
            try:
                from util.uvloop_compat import maybe_install_uvloop  # type: ignore
            except Exception:
                maybe_install_uvloop = None  # type: ignore
        try:
            if callable(maybe_install_uvloop):
                maybe_install_uvloop()
        except Exception:
            pass
        rc = asyncio.run(_main())
    except KeyboardInterrupt:
        rc = 130
    raise SystemExit(rc)
