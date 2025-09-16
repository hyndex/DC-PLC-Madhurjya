#!/usr/bin/env python3
"""
Send an SDP (SECC Discovery Protocol) request over UDP (IPv6) and print
the TCP port/address the SECC advertises.

Usage:
  python scripts/send_sdp.py --iface eth0 [--timeout 2.0]

Notes:
- This targets the link-local all-nodes multicast (ff02::1) on the given iface.
- Use the printed host/port to connect with scripts/evcc_min_flow.py.
"""
from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
from typing import Tuple

from iso15118.shared.messages.enums import Protocol, ISOV2PayloadTypes
from iso15118.shared.messages.sdp import SDPRequest, Security, Transport, SDPResponse
from iso15118.shared.messages.v2gtp import V2GTPMessage


def if_nametoindex(name: str) -> int:
    try:
        return socket.if_nametoindex(name)
    except Exception:
        # Fallback via netlink sysfs
        path = f"/sys/class/net/{name}/ifindex"
        try:
            return int(open(path).read().strip())
        except Exception as e:
            raise RuntimeError(f"Failed to get ifindex for {name}: {e}")


def make_sdp_req() -> bytes:
    req = SDPRequest(Security.NO_TLS, Transport.TCP).to_payload()
    v2g = V2GTPMessage(Protocol.UNKNOWN, ISOV2PayloadTypes.SDP_REQUEST, req)
    return v2g.to_bytes()


def parse_sdp_res(data: bytes) -> Tuple[str, int]:
    msg = V2GTPMessage.from_bytes(Protocol.UNKNOWN, data)
    if int(msg.payload_type) != 0x9001:
        raise RuntimeError(f"Unexpected payload type: {msg.payload_type}")
    sdp = SDPResponse.from_payload(msg.payload)
    # Convert raw IPv6 bytes to printable address
    ip_hex = int.from_bytes(sdp.ip_address, "big")
    host = str((ip_hex).to_bytes(16, "big"))  # not used; repr() is nicer
    # Use ipaddress to pretty print
    import ipaddress

    host = ipaddress.IPv6Address(ip_hex).compressed
    return host, int(sdp.port)


def main() -> int:
    ap = argparse.ArgumentParser(description="Send SDP request and print SECC TCP endpoint")
    ap.add_argument("--iface", required=True, help="Interface to send on (e.g., eth0)")
    ap.add_argument("--timeout", type=float, default=2.0)
    args = ap.parse_args()

    ifidx = if_nametoindex(args.iface)
    dst = ("ff02::1", 15118, 0, ifidx)

    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    try:
        sock.settimeout(args.timeout)
        # Set outgoing interface for multicast
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, struct.pack("I", ifidx))
        # Bind to wildcard to receive the unicast response
        sock.bind(("::", 0))
        req = make_sdp_req()
        sock.sendto(req, dst)
        data, addr = sock.recvfrom(1024)
        host, port = parse_sdp_res(data)
        print(f"host={host} port={port} from={addr[0]}%{args.iface}")
        return 0
    except socket.timeout:
        print("timeout waiting for SDP response", file=sys.stderr)
        return 2
    finally:
        try:
            sock.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
