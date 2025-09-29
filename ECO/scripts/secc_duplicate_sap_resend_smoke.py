#!/usr/bin/env python3
"""Smoke test: verify SECC duplicate resend on identical SAP request.

Requires EXI codec availability (py4j + Java). If not available, prints SKIP.

Procedure:
1) Start a dummy EV TCP server that sends a SupportedAppProtocolReq.
2) Read SAP response from SECC.
3) Send the exact same SAP request again within the duplicate window.
4) Read SAP response and verify it is byte-identical to the first response.
"""

import asyncio
import sys
from pathlib import Path

# Ensure local 'src' takes precedence for iso15118 imports
HERE = Path(__file__).resolve().parent.parent
LOCAL_ISO15118_ROOT = HERE / "src" / "iso15118"
if (LOCAL_ISO15118_ROOT / "iso15118" / "__init__.py").is_file():
    p = str(LOCAL_ISO15118_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

from iso15118.secc.comm_session_handler import SECCCommunicationSession
from iso15118.secc.secc_settings import Config
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


def _wrap_v2gtp(payload: bytes) -> bytes:
    return V2GTPMessage(Protocol.UNKNOWN, ISOV2PayloadTypes.EXI_ENCODED, payload).to_bytes()


def _build_sap_req_bytes() -> bytes:
    app = AppProtocol(
        protocol_ns=Protocol.ISO_15118_2.ns.value,
        major_version=2,
        minor_version=0,
        schema_id=1,
        priority=1,
    )
    sap = SupportedAppProtocolReq(app_protocol=[app])
    payload = EXI().to_exi(sap, Namespace.SAP)
    return _wrap_v2gtp(payload)


async def _read_v2gtp(reader: asyncio.StreamReader, timeout: float) -> bytes:
    hdr = await asyncio.wait_for(reader.readexactly(8), timeout=timeout)
    length = int.from_bytes(hdr[4:8], "big")
    body = b""
    if length > 0:
        body = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    return hdr + body


async def _start_ev_server(host: str, port: int, ready_evt: asyncio.Event, rx_q: asyncio.Queue):
    sap_bytes = _build_sap_req_bytes()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            # 1) Send first SAP
            writer.write(sap_bytes)
            await writer.drain()
            # 2) Read first response
            resp1 = await _read_v2gtp(reader, 2.0)
            await rx_q.put(resp1)
            # 3) Send duplicate SAP
            writer.write(sap_bytes)
            await writer.drain()
            # 4) Read duplicate response
            resp2 = await _read_v2gtp(reader, 2.0)
            await rx_q.put(resp2)
            await asyncio.sleep(0.2)
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(_handle, host, port)
    ready_evt.set()
    return server


async def main():
    if not _exi_available():
        print("result: SKIP (EXI codec unavailable)")
        return 0

    host = "127.0.0.1"
    ready = asyncio.Event()
    rx_q: asyncio.Queue = asyncio.Queue()
    server = await _start_ev_server(host, 0, ready, rx_q)
    sockets = server.sockets or []
    if not sockets:
        print("Server failed to start")
        return 2
    port = sockets[0].getsockname()[1]
    await ready.wait()
    try:
        reader, writer = await asyncio.open_connection(host, port)
        q: asyncio.Queue = asyncio.Queue()
        cfg = Config()
        cfg.load_envs(env_path=None)
        evse = _DummyEVSEController()
        secc = SECCCommunicationSession((reader, writer), q, cfg, evse, evse_id="EVSE-TEST-01")
        task = asyncio.create_task(secc.start(timeout=2.0))
        # Expect two SAP responses to be enqueued in rx_q by the EV server code
        resp1 = await asyncio.wait_for(rx_q.get(), timeout=5.0)
        resp2 = await asyncio.wait_for(rx_q.get(), timeout=5.0)
        ok = resp1 == resp2 and len(resp1) > 0
        # Terminate session task
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except Exception:
            pass
        print("result:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        server.close()
        await server.wait_closed()


class _DummyEVSEController(EVSEControllerInterface):
    async def set_status(self, status: ServiceStatus) -> None:
        return None

    async def get_evse_id(self, protocol):
        return "EVSE-TEST-01"

    async def get_supported_energy_transfer_modes(self, protocol):
        return []

    async def get_schedule_exchange_params(self, *args, **kwargs):
        return None

    async def get_energy_service_list(self):
        return None

    def is_eim_authorized(self) -> bool:
        return True

    async def is_authorized(self, *args, **kwargs):
        class _Resp:
            authorization_status = None
            certificate_response_status = None

        return _Resp()

    async def set_hlc_charging(self, is_ongoing: bool) -> None:
        return None

    async def update_data_link(self, action: SessionStopAction) -> None:
        return None

    async def set_present_protocol_state(self, state):
        return None

    async def get_15118_ev_certificate(self, *args, **kwargs) -> str:
        return ""

    def ready_to_charge(self) -> bool:
        return True

    # The rest of abstract methods are unused in this flow and return neutral values
    async def get_sa_schedule_list(self, *args, **kwargs):
        return None

    async def get_sa_schedule_list_dinspec(self, *args, **kwargs):
        return None

    async def get_meter_info_v2(self):
        return None

    async def get_meter_info_v20(self):
        return None

    async def get_supported_providers(self):
        return None

    async def get_cp_state(self):
        from iso15118.shared.messages.enums import CpState

        return CpState.C2

    async def service_renegotiation_supported(self) -> bool:
        return False

    async def stop_charger(self) -> None:
        return None

    async def get_ac_evse_status(self):
        return None

    async def get_ac_charge_params_v2(self):
        return None

    async def get_ac_charge_params_v20(self, energy_service=None):
        return None

    async def get_evse_status(self):
        return None

    async def is_contactor_closed(self):
        return None

    async def is_contactor_opened(self) -> bool:
        return True

    async def get_dc_evse_status(self):
        return None

    async def get_dc_charge_params_v2(self):
        return None

    async def get_dc_charge_params_v20(self, energy_service=None):
        return None

    async def get_dc_charge_parameter_limits_v20(self, *args, **kwargs):
        return None

    async def get_ac_charge_parameter_limits_v20(self, *args, **kwargs):
        return None

    async def get_dc_charge_loop_params_v20(self, *args, **kwargs):
        return None

    async def get_ac_charge_loop_params_v20(self, *args, **kwargs):
        return None

    async def send_display_params(self):
        return None

    async def send_rated_limits(self):
        return None

    async def get_service_parameter_list(self, service_id: int):
        return None

    async def get_dc_charge_parameters(self):
        return None

    async def start_cable_check(self):
        return None

    async def get_cable_check_status(self):
        return None

    async def send_charging_command(
        self,
        evse_present_voltage=None,
        evse_present_current=None,
    ):
        return None

    async def is_evse_current_limit_achieved(self):
        return False

    async def is_evse_voltage_limit_achieved(self):
        return False

    async def is_evse_power_limit_achieved(self) -> bool:
        return False


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

