from __future__ import annotations

import time
from typing import Optional, List, Union, Dict

from src.evse_hal.interfaces import EVSEHardware
from iso15118.secc.controller.simulator import SimEVSEController
from iso15118.secc.controller.interface import (
    AuthorizationResponse,
    ServiceStatus,
)
from iso15118.shared.messages.enums import (
    AuthorizationStatus,
    CpState,
    Protocol,
    EnergyTransferModeEnum,
)
from iso15118.shared.messages.iso15118_2.datatypes import MeterInfo as MeterInfoV2
from iso15118.shared.messages.iso15118_20.common_types import MeterInfo as MeterInfoV20
from iso15118.shared.states import State
import logging

logger = logging.getLogger("hlc")


class HalEVSEController(SimEVSEController):
    """SECC EVSEController backed by the EVSE HAL.

    Extends the simulator with real meter/contactor readings from the HAL.
    """

    def __init__(self, hal: EVSEHardware):
        super().__init__()
        self._hal = hal

    async def set_status(self, status: ServiceStatus) -> None:
        # Could map to LEDs or system state in real hardware
        return await super().set_status(status)

    async def get_evse_id(self, protocol: Protocol) -> str:
        return await super().get_evse_id(protocol)

    async def get_supported_energy_transfer_modes(
        self, protocol: Protocol
    ) -> List[EnergyTransferModeEnum]:
        return await super().get_supported_energy_transfer_modes(protocol)

    def is_eim_authorized(self) -> bool:
        return True

    async def is_authorized(
        self,
        id_token: Optional[str] = None,
        id_token_type: Optional[int] = None,
        certificate_chain: Optional[bytes] = None,
        hash_data: Optional[List[Dict[str, str]]] = None,
    ) -> AuthorizationResponse:
        return AuthorizationResponse(authorization_status=AuthorizationStatus.ACCEPTED)

    async def get_meter_info_v2(self) -> MeterInfoV2:
        m = self._hal.meter()
        return MeterInfoV2(
            meter_id="HAL-Meter",
            meter_reading=int(m.get_energy_Wh()),
            t_meter=time.time(),
        )

    async def get_meter_info_v20(self) -> MeterInfoV20:
        m = self._hal.meter()
        return MeterInfoV20(
            meter_id="HAL-Meter",
            charged_energy_reading_wh=int(m.get_energy_Wh()),
            meter_timestamp=time.time(),
        )

    async def is_contactor_closed(self) -> Optional[bool]:
        return self._hal.contactor().is_closed()

    async def is_contactor_opened(self) -> bool:
        return not self._hal.contactor().is_closed()

    async def get_cp_state(self) -> CpState:
        # Approximate mapping; real hardware would inspect CP voltage waveform
        state = self._hal.cp().get_state() or "B"
        return CpState.C2 if state in ("C", "D") else CpState.B1

    async def stop_charger(self) -> None:
        self._hal.contactor().set_closed(False)

    async def set_present_protocol_state(self, state: State):
        # Call parent for logging
        try:
            await super().set_present_protocol_state(state)  # type: ignore
        except Exception:
            pass
        # Attempt to emit BMS demand snapshot on each protocol state transition
        try:
            ctx = self.get_ev_data_context()  # type: ignore[attr-defined]
        except Exception:
            ctx = None
        snapshot = None
        if ctx is not None:
            try:
                snapshot = {
                    "present_soc": getattr(ctx, "present_soc", None),
                    "present_voltage": getattr(ctx, "present_voltage", None),
                    "target_voltage": getattr(ctx, "target_voltage", None),
                    "target_current": getattr(ctx, "target_current", None),
                    "max_current_limit": getattr(ctx, "max_current_limit", None),
                    "evcc_id": getattr(ctx, "evcc_id", None),
                }
            except Exception:
                snapshot = None
        logger.info(
            "ISO15118 state",
            extra={
                "state": str(state),
                **({"bms": snapshot} if snapshot else {}),
            },
        )
        # Publish to HLC manager if available
        try:
            from src.hlc.manager import hlc

            hlc.set_protocol_state(state)
        except Exception:
            pass
