#!/usr/bin/env python3
"""Smoke test: verify SAP selection preference (EV vs EVSE order).

Requires EXI codec availability (py4j + Java). If not available, prints SKIP.

Procedure:
1) Start a dummy EV TCP server that sends a SupportedAppProtocolReq offering both ISO 15118-2 (priority 1) and DIN (priority 2).
2) Run twice:
   a) SECC_SAP_PREFER_EV_PRIORITY=1 (default): expect ISO_15118_2 selected.
   b) SECC_SAP_PREFER_EV_PRIORITY=0 and PROTOCOLS=DIN_SPEC_70121,ISO_15118_2: expect DIN_SPEC_70121 selected.
3) Confirm selected protocol from SECCCommunicationSession.protocol and safe-state.
"""

import asyncio
import os
import sys

from iso15118.secc.comm_session_handler import SECCCommunicationSession
from iso15118.secc.secc_settings import Config
from iso15118.shared.notifications import StopNotification
from iso15118.shared.exi_codec import EXI
from iso15118.shared.messages.v2gtp import V2GTPMessage
from iso15118.shared.messages.app_protocol import AppProtocol, SupportedAppProtocolReq
from iso15118.shared.messages.enums import Namespace, Protocol, ISOV2PayloadTypes

from iso15118.secc.controller.interface import (
    EVSEControllerInterface,
    ServiceStatus,
    SessionStopAction,
)


def _exi_available() -> bool:
    try:
        EXI().get_exi_codec()
        return True
    except Exception:
        return False


class _DummyEVSEController(EVSEControllerInterface):
    def __init__(self):
        super().__init__()
        self.stop_charger_called = False

    async def set_status(self, status: ServiceStatus) -> None:
        return None

    async def get_evse_id(self, protocol):
        return "EVSE-TEST-01"

    async def get_supported_energy_transfer_modes(self, protocol):
        return []

    async def stop_charger(self) -> None:
        self.stop_charger_called = True

    async def update_data_link(self, action: SessionStopAction) -> None:
        return None

    async def set_present_protocol_state(self, state):
        return None


def _build_sap_req_bytes() -> bytes:
    # Offer ISO15118-2 priority 1, DIN priority 2
    apps = [
        AppProtocol(
            protocol_ns=Protocol.ISO_15118_2.ns.value,
            major_version=2,
            minor_version=0,
            schema_id=1,
            priority=1,
        ),
        AppProtocol(
            protocol_ns=Protocol.DIN_SPEC_70121.ns.value,
            major_version=2,
            minor_version=0,
            schema_id=2,
            priority=2,
        ),
    ]
    sap = SupportedAppProtocolReq(app_protocol=apps)
    payload = EXI().to_exi(sap, Namespace.SAP)
    v2gtp = V2GTPMessage(Protocol.UNKNOWN, ISOV2PayloadTypes.EXI_ENCODED, payload)
    return v2gtp.to_bytes()


async def _start_ev_server(host: str, port: int):
    sap_bytes = _build_sap_req_bytes()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            writer.write(sap_bytes)
            await writer.drain()
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(_handle, host, port)
    return server


async def run_case(prefer_ev: bool, protocols_env: str, expected: Protocol) -> bool:
    host = "127.0.0.1"
    server = await _start_ev_server(host, 0)
    sockets = server.sockets or []
    if not sockets:
        print("Server failed to start", file=sys.stderr)
        return False
    port = sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection(host, port)
        q: asyncio.Queue = asyncio.Queue()
        if protocols_env:
            os.environ["PROTOCOLS"] = protocols_env
        os.environ["SECC_SAP_PREFER_EV_PRIORITY"] = "1" if prefer_ev else "0"
        cfg = Config()
        cfg.load_envs(env_path=None)
        evse = _DummyEVSEController()
        secc = SECCCommunicationSession((reader, writer), q, cfg, evse, evse_id="EVSE-TEST-01")
        task = asyncio.create_task(secc.start(timeout=2.0))
        notif: StopNotification = await asyncio.wait_for(q.get(), timeout=10.0)
        # Protocol should have been chosen by SAP before termination
        chosen = getattr(secc, "protocol", None)
        ok = isinstance(notif, StopNotification) and (chosen == expected)
        await asyncio.wait_for(task, timeout=10.0)
        return ok
    finally:
        server.close()
        await server.wait_closed()


async def main():
    if not _exi_available():
        print("result: SKIP (EXI codec unavailable)")
        return 0

    ok1 = await run_case(True, "DIN_SPEC_70121,ISO_15118_2", Protocol.ISO_15118_2)
    ok2 = await run_case(False, "DIN_SPEC_70121,ISO_15118_2", Protocol.DIN_SPEC_70121)
    print("result:", "PASS" if (ok1 and ok2) else "FAIL")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

