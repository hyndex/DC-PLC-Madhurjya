#!/usr/bin/env python3
"""
EVCC-side fault injection helper to exercise SECC robustness to corruption and loss.

Usage examples:

  # Send 3 malformed EXI frames (valid V2GTP header, random payload)
  python scripts/evcc_fault_injector.py --host 127.0.0.1 --port 65000 --mode corrupt-exi --count 3 --size 64

  # Send the same frame twice quickly to trigger duplicate handling
  python scripts/evcc_fault_injector.py --host 127.0.0.1 --port 65000 --mode duplicate --payload-hex DEADBEEF

  # Craft invalid header (wrong inverse protocol byte)
  python scripts/evcc_fault_injector.py --host 127.0.0.1 --port 65000 --mode bad-header

Notes:
 - Duplicate resend requires the SECC to have sent at least one response before
   the duplicate arrives; run this while a session is active or prime the session first.
 - This script does not build valid EXI messages; it is intended to probe loss/corruption paths.
"""

import argparse
import asyncio
import os
import random
import sys
from typing import Optional


def _mk_v2gtp(protocol: str, payload_type: int, payload: bytes) -> bytes:
    # V2GTP header: [0]=0x01, [1]=0xFE, [2:4]=payload type, [4:8]=len
    if protocol not in ("iso2", "v20"):
        raise ValueError("protocol must be 'iso2' or 'v20'")
    header = bytearray(8)
    header[0] = 0x01
    header[1] = 0xFE
    header[2:4] = int(payload_type).to_bytes(2, "big")
    header[4:8] = len(payload).to_bytes(4, "big")
    return bytes(header) + payload


def _mk_bad_header(payload: bytes) -> bytes:
    # Break the inverse protocol version
    header = bytearray(8)
    header[0] = 0x01
    header[1] = 0x00  # invalid inverse byte
    header[2:4] = (0x8001).to_bytes(2, "big")
    header[4:8] = len(payload).to_bytes(4, "big")
    return bytes(header) + payload


async def _send_and_maybe_read(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, data: bytes, read_timeout: float) -> Optional[bytes]:
    writer.write(data)
    await writer.drain()
    if read_timeout <= 0:
        return None
    try:
        # Attempt to read a V2GTP header from SECC; do not parse further here
        hdr = await asyncio.wait_for(reader.readexactly(8), timeout=read_timeout)
    except Exception:
        return None
    try:
        length = int.from_bytes(hdr[4:8], "big")
        if length > 0:
            body = await asyncio.wait_for(reader.readexactly(length), timeout=read_timeout)
            return hdr + body
        return hdr
    except Exception:
        return hdr


async def main():
    ap = argparse.ArgumentParser(description="EVCC fault injector (TCP)")
    ap.add_argument("--host", required=True, help="SECC host (IP)")
    ap.add_argument("--port", type=int, required=True, help="SECC TCP port")
    ap.add_argument("--mode", choices=["corrupt-exi", "duplicate", "bad-header"], required=True)
    ap.add_argument("--protocol", choices=["iso2", "v20"], default="iso2")
    ap.add_argument("--payload-type", type=lambda x: int(x, 0), default=0x8001, help="Payload type (default 0x8001 EXI_ENCODED)")
    ap.add_argument("--payload-hex", help="Hex payload to send (overrides --size)")
    ap.add_argument("--size", type=int, default=32, help="Random payload size if --payload-hex not set")
    ap.add_argument("--count", type=int, default=1, help="Number of frames to send")
    ap.add_argument("--interval", type=float, default=0.1, help="Interval between frames (s)")
    ap.add_argument("--read-timeout", type=float, default=0.5, help="Read timeout after send (s); 0 disables reading")
    args = ap.parse_args()

    if args.payload_hex:
        try:
            payload = bytes.fromhex(args.payload_hex)
        except Exception:
            print("Invalid --payload-hex", file=sys.stderr)
            sys.exit(2)
    else:
        random.seed(os.urandom(8))
        payload = bytes(random.getrandbits(8) for _ in range(max(0, int(args.size))))

    reader, writer = await asyncio.open_connection(args.host, args.port)
    try:
        if args.mode == "bad-header":
            frame = _mk_bad_header(payload)
            await _send_and_maybe_read(reader, writer, frame, args.read_timeout)
            return

        # Valid V2GTP header; payload may be arbitrary
        frame = _mk_v2gtp(args.protocol, args.payload_type, payload)

        if args.mode == "corrupt-exi":
            for i in range(max(1, args.count)):
                _ = await _send_and_maybe_read(reader, writer, frame, args.read_timeout)
                await asyncio.sleep(max(0.0, args.interval))
        elif args.mode == "duplicate":
            # Send the same frame repeatedly to trigger duplicate detection
            for i in range(max(1, args.count)):
                _ = await _send_and_maybe_read(reader, writer, frame, args.read_timeout)
                await asyncio.sleep(max(0.0, args.interval))
        else:
            raise AssertionError("unreachable")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())

