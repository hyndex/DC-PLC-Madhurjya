#!/usr/bin/env python3
"""
Passive HPAV (HomePlug AV) sniff to extract EV MAC as early as possible.

Listens on the PLC interface for HomePlug AV frames (ethertype 0x88E1) and
prints the source MAC of the first observed frame. If the frame is a
CM_SLAC_PARM.REQ it also dumps the first bytes of the payload.

Usage:
  sudo -E python scripts/sniff_ev_mac.py --iface eth1 --timeout 60
  (No PYTHONPATH needed; the script auto-adds local PySLAC if present.)
"""
from __future__ import annotations

import argparse
import asyncio
import binascii
import sys
from pathlib import Path

# Make local PySLAC importable without requiring external installation
try:
    _ROOT = Path(__file__).resolve().parents[1]
    _PYSLAC_BASE = _ROOT / "src" / "pyslac"
    if (_PYSLAC_BASE / "pyslac" / "__init__.py").is_file():
        p = str(_PYSLAC_BASE)
        if p not in sys.path:
            sys.path.insert(0, p)
except Exception:
    pass

try:
    from pyslac.layer_2_headers import EthernetHeader, HomePlugHeader
    from pyslac.enums import CM_SLAC_PARM, MMTYPE_REQ, FramesSizes, ETH_TYPE_HPAV
    from pyslac.utils import get_if_hwaddr
    from pyslac.sockets.async_linux_socket import create_socket, readeth
except Exception as e:
    print("Import error: ensure PYTHONPATH includes src/pyslac", e, file=sys.stderr)
    sys.exit(2)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", default="eth1")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    s = create_socket(args.iface, port=0)
    try:
        local_mac = get_if_hwaddr(args.iface)
    except Exception:
        local_mac = None
    deadline = asyncio.get_event_loop().time() + args.timeout
    print(f"[sniff] Waiting up to {args.timeout}s on {args.iface} for CM_SLAC_PARM.REQ ...", flush=True)
    while asyncio.get_event_loop().time() < deadline:
        try:
            data = await asyncio.wait_for(readeth(s, args.iface, rcv_frame_size=60), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        try:
            eth = EthernetHeader.from_bytes(data)
            hp = HomePlugHeader.from_bytes(data)
        except Exception:
            continue
        # pyslac header uses 'ether_type' naming
        if getattr(eth, 'ether_type', None) != ETH_TYPE_HPAV:
            continue
        # Ignore frames originating from our own interface
        if local_mac is not None and eth.src_mac == local_mac:
            continue
        # Only accept CM_SLAC_PARM.REQ as authoritative EV source
        if hp.mm_type == (CM_SLAC_PARM | MMTYPE_REQ):
            ev_mac = ":".join(f"{b:02x}" for b in eth.src_mac)
            print("[sniff] EV MAC from CM_SLAC_PARM.REQ:", ev_mac)
            payload = data[14+5:]
            print("[sniff] CM_SLAC_PARM.REQ payload (hex, first 64B):", binascii.hexlify(payload[:64]).decode())
            return 0
        # Otherwise keep waiting for the first CM_SLAC_PARM.REQ
        # (Avoid reporting potentially misleading HPAV sources.)
    print("[sniff] No CM_SLAC_PARM.REQ observed (timeout).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
