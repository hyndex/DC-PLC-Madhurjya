#!/usr/bin/env python3
"""
Minimal EVCC-side handshake to exercise SECC end-to-end behavior with scenarios:
- SAP negotiation (SupportedAppProtocol)
- SessionSetup (ISO 15118-2)
- ServiceDiscovery (ISO 15118-2)
- Optional duplicate injection and corruption injection

This focuses on transport robustness, duplicate resend, and decode-tolerance paths
without implementing the full payment/charge loop.
"""

import argparse
import asyncio
import os
import sys
from typing import Tuple

from iso15118.shared.exi_codec import EXI
from iso15118.shared.messages.app_protocol import AppProtocol, SupportedAppProtocolReq, ResponseCodeSAP
from iso15118.shared.messages.enums import ISOV2PayloadTypes, Namespace, Protocol
from iso15118.shared.messages.iso15118_2.body import Body, ServiceDiscoveryReq, SessionSetupReq
from iso15118.shared.messages.iso15118_2.header import MessageHeader
from iso15118.shared.messages.iso15118_2.msgdef import V2GMessage as V2GMessageV2
from iso15118.shared.messages.v2gtp import V2GTPMessage


async def _send_and_recv(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, data: bytes, timeout: float = 2.0) -> bytes:
    writer.write(data)
    await writer.drain()
    # Read V2GTP header
    hdr = await asyncio.wait_for(reader.readexactly(8), timeout=timeout)
    length = int.from_bytes(hdr[4:8], "big")
    body = b""
    if length:
        body = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    return hdr + body


def _wrap_v2gtp(protocol: Protocol, payload_type: ISOV2PayloadTypes, payload: bytes) -> bytes:
    return V2GTPMessage(protocol, payload_type, payload).to_bytes()


def _make_sap_req() -> bytes:
    # Offer ISO 15118-2 first
    app = [
        AppProtocol(
            protocol_ns=Protocol.ISO_15118_2.ns,
            major_version=1,
            minor_version=0,
            schema_id=1,
            priority=1,
        )
    ]
    req = SupportedAppProtocolReq(app_protocol=app)
    exi = EXI().to_exi(req, Namespace.SAP)
    return _wrap_v2gtp(Protocol.UNKNOWN, ISOV2PayloadTypes.EXI_ENCODED, exi)


def _make_session_setup_req(evcc_id_hex: str) -> Tuple[bytes, str]:
    # SessionID must be hex string (8 bytes → 16 hex chars); use zeros initially
    header = MessageHeader(session_id="0" * 16)
    body = Body(session_setup_req=SessionSetupReq(evcc_id=evcc_id_hex))
    msg = V2GMessageV2(header=header, body=body)
    exi = EXI().to_exi(msg, Namespace.ISO_V2_MSG_DEF)
    return _wrap_v2gtp(Protocol.ISO_15118_2, ISOV2PayloadTypes.EXI_ENCODED, exi), str(msg)


def _make_service_discovery_req() -> Tuple[bytes, str]:
    header = MessageHeader(session_id="0" * 16)
    body = Body(service_discovery_req=ServiceDiscoveryReq())
    msg = V2GMessageV2(header=header, body=body)
    exi = EXI().to_exi(msg, Namespace.ISO_V2_MSG_DEF)
    return _wrap_v2gtp(Protocol.ISO_15118_2, ISOV2PayloadTypes.EXI_ENCODED, exi), str(msg)


async def run_flow(host: str, port: int, duplicate_sd: bool, corrupt_after_sap: bool, read_to: float, pause_before_sd: float) -> int:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        # 1) SAP
        sap_req = _make_sap_req()
        resp = await _send_and_recv(reader, writer, sap_req, timeout=read_to)
        payload = resp[8:]
        sap = EXI().from_exi(payload, Namespace.SAP)
        if not hasattr(sap, "response_code") or sap.response_code not in (
            ResponseCodeSAP.NEGOTIATION_OK,
            ResponseCodeSAP.MINOR_DEVIATION,
        ):
            print("SAP failed:", sap)
            return 2
        print("SAP OK:", getattr(sap, "response_code", None))

        if corrupt_after_sap:
            # Flip one byte in a valid SessionSetupReq to simulate corruption
            frame, name = _make_session_setup_req("A1B2C3D4E5F6")
            # Corrupt payload body
            frame = frame[:12] + bytes([frame[12] ^ 0xFF]) + frame[13:]
            try:
                _ = await _send_and_recv(reader, writer, frame, timeout=read_to)
            except Exception:
                pass

        # 2) SessionSetup
        frame, name = _make_session_setup_req("A1B2C3D4E5F6")
        resp = await _send_and_recv(reader, writer, frame, timeout=read_to)
        v2g = EXI().from_exi(resp[8:], Namespace.ISO_V2_MSG_DEF)
        print("RX:", str(v2g))

        # 3) ServiceDiscovery
        if pause_before_sd > 0:
            # Intentionally delay to exercise SECC timeout behavior
            await asyncio.sleep(pause_before_sd)
        sd_frame, sd_name = _make_service_discovery_req()
        resp1 = await _send_and_recv(reader, writer, sd_frame, timeout=read_to)
        v2g1 = EXI().from_exi(resp1[8:], Namespace.ISO_V2_MSG_DEF)
        print("RX:", str(v2g1))

        if duplicate_sd:
            # Send the same ServiceDiscoveryReq again to trigger duplicate resend
            resp2 = await _send_and_recv(reader, writer, sd_frame, timeout=read_to)
            print("Duplicate response len:", len(resp2))
        return 0
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    ap = argparse.ArgumentParser(description="EVCC minimal handshake and robustness scenarios")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--duplicate-sd", action="store_true", help="Send duplicate ServiceDiscoveryReq")
    ap.add_argument("--corrupt-after-sap", action="store_true", help="Inject a corrupted frame right after SAP")
    ap.add_argument("--read-timeout", type=float, default=2.0)
    ap.add_argument("--pause-before-sd", type=float, default=0.0, help="Pause before ServiceDiscovery request to trigger SECC timeout if high")
    args = ap.parse_args()

    rc = await run_flow(args.host, args.port, args.duplicate_sd, args.corrupt_after_sap, args.read_timeout, args.pause_before_sd)
    raise SystemExit(rc)


if __name__ == "__main__":
    asyncio.run(main())
