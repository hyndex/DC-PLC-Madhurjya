from __future__ import annotations

import time
from typing import Optional, List, Union, Dict
import os

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
    IsolationLevel,
    UnitSymbol,
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

    # --- DC parameters ---
    # The default SimEVSEController returns toy values (e.g., 40 V, 40 A ripple),
    # which can cause real EVs to abort after CPD. Override with realistic limits.
    async def get_dc_charge_parameters(self):  # type: ignore[override]
        from iso15118.shared.messages.datatypes import (
            DCEVSEChargeParameter,
            DCEVSEStatus,
            DCEVSEStatusCode,
            EVSENotification as EVSENotificationV2,
            PVEVSEMaxPowerLimit,
            PVEVSEMaxCurrentLimit,
            PVEVSEMaxVoltageLimit,
            PVEVSEMinCurrentLimit,
            PVEVSEMinVoltageLimit,
            PVEVSEPeakCurrentRipple,
        )

        def env_float(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, default))
            except Exception:
                return default

        # Defaults are conservative but realistic for many DC chargers.
        max_v = env_float("EVSE_DC_MAX_VOLTAGE_V", 920.0)  # V
        max_a = env_float("EVSE_DC_MAX_CURRENT_A", 300.0)  # A
        max_w = env_float("EVSE_DC_MAX_POWER_W", max_v * max_a)  # W
        min_v = env_float("EVSE_DC_MIN_VOLTAGE_V", 150.0)  # V
        min_a = env_float("EVSE_DC_MIN_CURRENT_A", 0.0)    # A
        ripple_a = env_float("EVSE_DC_PEAK_RIPPLE_A", 5.0) # A

        # Choose multipliers to keep value in a compact range
        def pv(value: float):
            # Pick multiplier so abs(value) in [1, 999]
            if value == 0:
                return 0, 0
            mul = 0
            v = abs(value)
            while v >= 1000:
                v /= 10.0
                mul += 1
            while v and v < 1:
                v *= 10.0
                mul -= 1
            return int(round(v)), mul

        max_w_val, max_w_mul = pv(max_w)
        max_a_val, max_a_mul = pv(max_a)
        max_v_val, max_v_mul = pv(max_v)
        min_a_val, min_a_mul = pv(min_a)
        min_v_val, min_v_mul = pv(min_v)
        ripple_val, ripple_mul = pv(ripple_a)

        return DCEVSEChargeParameter(
            dc_evse_status=DCEVSEStatus(
                notification_max_delay=100,
                evse_notification=EVSENotificationV2.NONE,
                evse_isolation_status=IsolationLevel.VALID,
                evse_status_code=DCEVSEStatusCode.EVSE_READY,
            ),
            evse_maximum_power_limit=PVEVSEMaxPowerLimit(
                multiplier=max_w_mul, value=max_w_val, unit=UnitSymbol.WATT
            ),
            evse_maximum_current_limit=PVEVSEMaxCurrentLimit(
                multiplier=max_a_mul, value=max_a_val, unit=UnitSymbol.AMPERE
            ),
            evse_maximum_voltage_limit=PVEVSEMaxVoltageLimit(
                multiplier=max_v_mul, value=max_v_val, unit=UnitSymbol.VOLTAGE
            ),
            evse_minimum_current_limit=PVEVSEMinCurrentLimit(
                multiplier=min_a_mul, value=min_a_val, unit=UnitSymbol.AMPERE
            ),
            evse_minimum_voltage_limit=PVEVSEMinVoltageLimit(
                multiplier=min_v_mul, value=min_v_val, unit=UnitSymbol.VOLTAGE
            ),
            evse_peak_current_ripple=PVEVSEPeakCurrentRipple(
                multiplier=ripple_mul, value=ripple_val, unit=UnitSymbol.AMPERE
            ),
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
