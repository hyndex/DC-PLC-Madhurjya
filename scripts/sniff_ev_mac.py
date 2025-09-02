#!/usr/bin/env python3
"""
Passive HPAV (HomePlug AV) sniff to extract EV MAC as early as possible.

Listens on the PLC interface for HomePlug AV frames (ethertype 0x88E1) and
prints the source MAC of the first observed frame. If the frame is a
CM_SLAC_PARM.REQ it also dumps the first bytes of the payload.

Usage:
  sudo -E env PYTHONPATH=$(pwd)/src/pyslac python scripts/sniff_ev_mac.py --iface eth1 --timeout 60
"""
from __future__ import annotations

import argparse
import asyncio
import binascii
import sys

try:
    from pyslac.layer_2_headers import EthernetHeader, HomePlugHeader
    from pyslac.enums import CM_SLAC_PARM, MMTYPE_REQ, FramesSizes, ETH_TYPE_HPAV
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
    deadline = asyncio.get_event_loop().time() + args.timeout
    print(f"[sniff] Waiting up to {args.timeout}s on {args.iface} for any HPAV frame ...", flush=True)
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
        if eth.eth_type != ETH_TYPE_HPAV:
            continue
        ev_mac = ":".join(f"{b:02x}" for b in eth.src_mac)
        print("[sniff] HPAV frame from EV MAC:", ev_mac)
        if hp.mm_type == (CM_SLAC_PARM | MMTYPE_REQ):
            payload = data[14+5:]
            print("[sniff] CM_SLAC_PARM.REQ payload (hex, first 64B):", binascii.hexlify(payload[:64]).decode())
        return 0
    print("[sniff] No HPAV frames observed (timeout).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
