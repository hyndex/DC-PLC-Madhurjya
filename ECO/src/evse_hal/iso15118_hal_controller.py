from __future__ import annotations

import time
from typing import Optional, List, Union, Dict
import asyncio
import os

from .interfaces import EVSEHardware
from .thermal import ThermalManager
from iso15118.secc.controller.simulator import SimEVSEController
try:
    from iso15118.secc.controller.interface import (
        AuthorizationResponse,
        ServiceStatus,
    )
except Exception:  # pragma: no cover - allow tests to stub minimal interface
    from dataclasses import dataclass

    @dataclass
    class AuthorizationResponse:  # type: ignore
        # Use a loose type to avoid importing iso15118 enums in test stubs
        authorization_status: object

    class ServiceStatus(str):  # type: ignore
        READY = "ready"
        STARTING = "starting"
        STOPPING = "stopping"
        ERROR = "error"
        BUSY = "busy"
try:
    from iso15118.shared.messages.enums import (
        AuthorizationStatus,
        CpState,
        Protocol,
        EnergyTransferModeEnum,
        IsolationLevel,
        UnitSymbol,
    )
except Exception:  # pragma: no cover - lightweight fallbacks for tests
    class AuthorizationStatus(str):
        ACCEPTED = "ACCEPTED"

    class CpState(str):
        A1 = "A1"
        B1 = "B1"
        C2 = "C2"
        D2 = "D2"
        E = "E"
        F = "F"
        UNKNOWN = "UNKNOWN"

    class Protocol(str):
        DIN_SPEC_70121 = "DIN_SPEC_70121"
        ISO_15118_2 = "ISO_15118_2"
        ISO_15118_20_AC = "ISO_15118_20_AC"
        ISO_15118_20_DC = "ISO_15118_20_DC"

    class EnergyTransferModeEnum(str):
        AC_SINGLE_PHASE_CORE = "AC_SINGLE_PHASE_CORE"
        AC_THREE_PHASE_CORE = "AC_THREE_PHASE_CORE"
        DC_CORE = "DC_CORE"
        DC_EXTENDED = "DC_EXTENDED"

    class IsolationLevel(str):
        VALID = "VALID"

    class UnitSymbol(str):
        WATT = "W"
        AMPERE = "A"
        VOLTAGE = "V"

try:
    from iso15118.shared.messages.iso15118_2.datatypes import MeterInfo as MeterInfoV2
except Exception:  # pragma: no cover - minimal stand-ins for tests
    from dataclasses import dataclass

    @dataclass
    class MeterInfoV2:  # type: ignore
        meter_id: str
        meter_reading: int
        t_meter: float

try:
    from iso15118.shared.messages.iso15118_20.common_types import MeterInfo as MeterInfoV20
except Exception:  # pragma: no cover
    from dataclasses import dataclass

    @dataclass
    class MeterInfoV20:  # type: ignore
        meter_id: str
        charged_energy_reading_wh: int
        meter_timestamp: float

try:
    from iso15118.shared.states import State
except Exception:  # pragma: no cover
    class State:  # type: ignore
        pass
import logging

logger = logging.getLogger("hlc")


class HalEVSEController(SimEVSEController):
    """SECC EVSEController backed by the EVSE HAL.

    Extends the simulator with real meter/contactor readings from the HAL.
    """

    def __init__(self, hal: EVSEHardware):
        super().__init__()
        self._hal = hal
        self._last_set_v: float = 0.0
        self._last_set_i: float = 0.0
        self._last_allowed_i: float = 0.0
        self._last_set_ts: float = time.time()
        # Last known diagnostic snapshots to avoid confusing placeholder values
        self._last_bms_snapshot = None
        self._last_evse_snapshot = None
        # Thermal and dynamic derating support
        self._thermal = ThermalManager()
        self._rated_dc_max_current_a: float = 300.0
        self._rated_dc_max_voltage_v: float = 920.0
        # Internal helper for CableCheck contactor raise-and-wait
        self._cc_close_issued: bool = False
        self._cc_close_ts: float = 0.0
        # Precharge acceleration guard (avoid repeating dc.cfg spam)
        self._precharge_accel_done: bool = False
        # Mode management (Low: 200–500 V, High: 400–1000 V) with hysteresis
        # EVSE_MODE: auto|low|high; when auto, pick on first reliable hint and freeze at PD(Start)
        try:
            self._mode_pref = os.environ.get("EVSE_MODE", "auto").strip().lower()
        except Exception:
            self._mode_pref = "auto"
        self._mode_locked: bool = False
        self._mode: str = "low"  # low|high
        # Hysteresis thresholds
        def _envf(key: str, default: float) -> float:
            try:
                return float(os.environ.get(key, default))
            except Exception:
                return float(default)
        self._hi_enter_v: float = _envf("EVSE_MODE_HI_ENTER_V", 460.0)
        self._hi_exit_v: float = _envf("EVSE_MODE_HI_EXIT_V", 440.0)

    async def set_status(self, status: ServiceStatus) -> None:
        # Could map to LEDs or system state in real hardware
        return await super().set_status(status)

    async def get_evse_id(self, protocol: Protocol) -> str:
        """Return EVSEID from environment when provided, else fallback.

        For ISO 15118-2/-20 we expect the standardized EVSEID format like
        "DE*PNC*E12345*1". Many EVs abort early if the EVSEID is a placeholder.

        The value can be provided via environment variable `EVSE_ID` (e.g., in
        `secc.env` or exported by the launcher). If not set, we fall back to the
        simulator default for DIN and a sane example for ISO 15118-2/-20.
        """
        try:
            # Prefer a dedicated ISO EVSEID if provided; fallback to EVSE_ID
            evse_id = (
                os.environ.get("ISO_EVSE_ID")
                or os.environ.get("EVSE_ID")
                or os.environ.get("EVSEID")
            )
            if evse_id:
                evse_id = evse_id.strip()
        except Exception:
            evse_id = None

        # DIN uses a different representation (hexBinary) with '*' mapped to nibble 0xA.
        # If a custom DIN ID is provided via ENV, use it. Otherwise, try to convert
        # a star-separated numeric ID to hex (e.g., "49*89*6360" -> "49A89A6360").
        if protocol == Protocol.DIN_SPEC_70121:
            try:
                din_hex = os.environ.get("EVSE_ID_DIN_HEX") or os.environ.get("DIN_EVSE_ID_HEX")
                if din_hex:
                    din_hex = din_hex.strip().upper()
                else:
                    raw = os.environ.get("EVSE_ID_DIN") or os.environ.get("DIN_EVSE_ID") or evse_id
                    if raw:
                        # Map '*' to 'A' nibble and drop separators
                        din_hex = raw.strip().upper().replace("*", "A").replace(":", "").replace("-", "")
                # Case 1: Proper hexBinary already
                if din_hex and len(din_hex) % 2 == 0 and all(c in "0123456789ABCDEF" for c in din_hex):
                    logger.info("DIN EVSEID (hexBinary)", extra={"evse_id_din_hex": din_hex})
                    return din_hex
                # Case 2: Provided a non-hex e-mobility style string (e.g., INJPSE0006360);
                # encode ASCII bytes as hex to satisfy hexBinary type.
                if raw and any(ch not in "0123456789ABCDEF" for ch in raw.strip().upper().replace(":","")):
                    try:
                        ascii_hex = raw.strip().encode("ascii", errors="ignore").hex().upper()
                        if ascii_hex and len(ascii_hex) % 2 == 0:
                            logger.info(
                                "DIN EVSEID derived from ASCII (hexBinary)",
                                extra={"raw": raw.strip(), "evse_id_din_hex": ascii_hex},
                            )
                            return ascii_hex
                    except Exception:
                        pass
                # Fallback warning
                if din_hex:
                    logger.warning("Invalid DIN EVSEID for hexBinary; falling back", extra={"provided": din_hex})
            except Exception:
                pass
            # Fallback simulator example
            fb = "49A89A6360"
            logger.info("Using fallback DIN EVSEID", extra={"evse_id_din_hex": fb})
            return fb

        # ISO 15118-2/-20: prefer provided EVSE_ID, else use a valid-looking default
        if evse_id:
            logger.info("ISO EVSEID", extra={"evse_id": evse_id})
            return evse_id
        fb_iso = "DE*PNC*E12345*1"
        logger.info("Using fallback ISO EVSEID", extra={"evse_id": fb_iso})
        return fb_iso

    async def get_supported_energy_transfer_modes(
        self, protocol: Protocol
    ) -> List[EnergyTransferModeEnum]:
        """Advertise AC-only or DC-only based on the HAL CP mode.

        Many EVs expect the EVSE to be consistent between the physical CP
        signaling and the capabilities it advertises over HLC. If the HAL is in
        AC/manual mode (IEC 61851 PWM), advertise AC modes only. If in DC mode,
        advertise DC modes only. Fallback to simulator defaults on error.
        """
        try:
            mode = None
            try:
                mode = getattr(self._hal, "cp_mode", lambda: None)()
            except Exception:
                mode = None
            # Normalize
            mode = (str(mode).strip().lower() if mode else None)
            if mode in ("ac", "manual"):
                # Offer common AC modes for ISO 15118-2/-20
                return [
                    EnergyTransferModeEnum.AC_SINGLE_PHASE_CORE,
                    EnergyTransferModeEnum.AC_THREE_PHASE_CORE,
                ]
            if mode == "dc":
                return [
                    EnergyTransferModeEnum.DC_CORE,
                    EnergyTransferModeEnum.DC_EXTENDED,
                ]
        except Exception:
            pass
        # Fallback to parent behavior if detection fails
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

    async def set_hlc_charging(self, is_ongoing: bool) -> None:
        """Close/open the EVSE contactor in sync with ISO15118 PowerDelivery.

        - When HLC charging starts (PowerDelivery Start), close the contactor and
          wait briefly for auxiliary feedback.
        - When HLC stops, do not force-open here; "stop_charger()" handles
          graceful ramp-down and opening with auxiliary verification.
        """
        # Bench mode: if EVSE_SIM_CONTACTOR is set, skip physical contactor but enable DC output
        try:
            if os.environ.get("EVSE_SIM_CONTACTOR", "0").strip().lower() not in ("0", "false", "no", ""):
                try:
                    # In bench/sim, tie HLC charging to DC output enable
                    getattr(self._hal, "dc_enable", lambda _on: None)(bool(is_ongoing))
                except Exception:
                    pass
                # Pre-arm minimal setpoints on Start to avoid 0A in first CD loop
                if is_ongoing:
                    try:
                        self._hal.supply().set_voltage(max(0.0, float(self._last_set_v)))
                        self._hal.supply().set_current_limit(min(5.0, float(self._rated_dc_max_current_a)))
                    except Exception:
                        pass
                # Freeze mode selection at Start
                if is_ongoing:
                    self._mode_locked = True
                return
        except Exception:
            pass
        try:
            cont = self._hal.contactor()
        except Exception:
            return
        if is_ongoing:
            try:
                cont.set_closed(True)
            except Exception:
                # Let ISO state machine detect and handle failure
                return
            # Await auxiliary confirmation up to ~3s (V2G2-860 expectation)
            deadline = asyncio.get_event_loop().time() + 3.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    if cont.is_closed():
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.05)
        else:
            # No-op here; stop_charger() performs controlled open and CP fallback
            return

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

    async def service_renegotiation_supported(self) -> bool:  # type: ignore[override]
        return True

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
        # Prefer explicit DC current rating; fall back to periph cfg (I_MAX)
        try:
            if "EVSE_DC_MAX_CURRENT_A" in os.environ:
                max_a = float(os.environ.get("EVSE_DC_MAX_CURRENT_A", "300.0"))
            elif "EVSE_PERIPH_CFG_I_MAX" in os.environ:
                max_a = float(os.environ.get("EVSE_PERIPH_CFG_I_MAX", "300.0"))
            else:
                max_a = env_float("EVSE_DC_MAX_CURRENT_A", 300.0)
        except Exception:
            max_a = env_float("EVSE_DC_MAX_CURRENT_A", 300.0)
        # Prefer explicit DC power cap; else periph cfg P_KW; else V*A
        try:
            if "EVSE_DC_MAX_POWER_W" in os.environ:
                max_w = float(os.environ.get("EVSE_DC_MAX_POWER_W", str(max_v * max_a)))
            else:
                p_kw = os.environ.get("EVSE_PERIPH_CFG_P_KW")
                max_w = float(p_kw) * 1000.0 if p_kw else (max_v * max_a)
        except Exception:
            max_w = max_v * max_a
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

        # Cache rated for dynamic use/advertisement
        self._rated_dc_max_current_a = float(max_a)
        self._rated_dc_max_voltage_v = float(max_v)

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

    # Advertise DC capability limits consistent with configured ratings
    async def get_evse_max_voltage_limit(self):  # type: ignore[override]
        try:
            max_v = float(os.environ.get("EVSE_DC_MAX_VOLTAGE_V", str(self._rated_dc_max_voltage_v)))
        except Exception:
            max_v = float(getattr(self, "_rated_dc_max_voltage_v", 920.0))
        from iso15118.shared.messages.datatypes import PVEVSEMaxVoltageLimit
        return PVEVSEMaxVoltageLimit(multiplier=0, value=int(round(max_v)), unit=UnitSymbol.VOLTAGE)

    async def get_evse_max_current_limit(self):  # type: ignore[override]
        try:
            if "EVSE_DC_MAX_CURRENT_A" in os.environ:
                max_a = float(os.environ.get("EVSE_DC_MAX_CURRENT_A", str(self._rated_dc_max_current_a)))
            elif "EVSE_PERIPH_CFG_I_MAX" in os.environ:
                max_a = float(os.environ.get("EVSE_PERIPH_CFG_I_MAX", str(self._rated_dc_max_current_a)))
            else:
                max_a = float(getattr(self, "_rated_dc_max_current_a", 300.0))
        except Exception:
            max_a = float(getattr(self, "_rated_dc_max_current_a", 300.0))
        from iso15118.shared.messages.datatypes import PVEVSEMaxCurrentLimit
        return PVEVSEMaxCurrentLimit(multiplier=0, value=int(round(max_a)), unit=UnitSymbol.AMPERE)

    async def get_evse_max_power_limit(self):  # type: ignore[override]
        try:
            max_v = float(os.environ.get("EVSE_DC_MAX_VOLTAGE_V", str(self._rated_dc_max_voltage_v)))
        except Exception:
            max_v = float(getattr(self, "_rated_dc_max_voltage_v", 920.0))
        try:
            if "EVSE_DC_MAX_CURRENT_A" in os.environ:
                max_a = float(os.environ.get("EVSE_DC_MAX_CURRENT_A", str(self._rated_dc_max_current_a)))
            elif "EVSE_PERIPH_CFG_I_MAX" in os.environ:
                max_a = float(os.environ.get("EVSE_PERIPH_CFG_I_MAX", str(self._rated_dc_max_current_a)))
            else:
                max_a = float(getattr(self, "_rated_dc_max_current_a", 300.0))
        except Exception:
            max_a = float(getattr(self, "_rated_dc_max_current_a", 300.0))
        # Prefer explicit power cap; fall back to periph config (P_KW) if present; else V*A
        try:
            if "EVSE_DC_MAX_POWER_W" in os.environ:
                max_w = float(os.environ.get("EVSE_DC_MAX_POWER_W", max_v * max_a))
            else:
                p_kw_env = os.environ.get("EVSE_PERIPH_CFG_P_KW")
                max_w = float(p_kw_env) * 1000.0 if p_kw_env else (max_v * max_a)
        except Exception:
            max_w = max_v * max_a
        # Choose multiplier/value such that 1 <= value < 1000
        def pv(value: float):
            if value <= 0:
                return 0, 0
            mul = 0
            v = float(value)
            while v >= 1000.0:
                v /= 10.0
                mul += 1
            while v < 1.0:
                v *= 10.0
                mul -= 1
            return int(round(v)), mul
        val, mul = pv(max_w)
        from iso15118.shared.messages.datatypes import PVEVSEMaxPowerLimit
        return PVEVSEMaxPowerLimit(multiplier=mul, value=val, unit=UnitSymbol.WATT)

    async def get_evse_present_voltage(self, protocol):  # type: ignore[override]
        # Allow supply simulation for bench bring-up without power electronics
        try:
            if os.environ.get("EVSE_SIM_SUPPLY", "0").strip().lower() not in ("0", "false", "no", ""):
                v = float(self._last_set_v)
                a = float(self._last_set_i)
            else:
                v, a = self._hal.supply().get_status()
        except Exception:
            v, a = float(self._last_set_v), float(self._last_set_i)
        # Optional fault injection: scale measurements to simulate mismatch
        try:
            sv = float(os.environ.get("EVSE_FAULT_SCALE_V", "1.0"))
            si = float(os.environ.get("EVSE_FAULT_SCALE_I", "1.0"))
            v, a = v * sv, a * si
        except Exception:
            pass
        # Update EVSE data context for downstream getters
        self.evse_data_context.present_voltage = float(v)
        self.evse_data_context.present_current = float(a)
        return await super().get_evse_present_voltage(protocol)

    async def get_evse_present_current(self, protocol):  # type: ignore[override]
        try:
            if os.environ.get("EVSE_SIM_SUPPLY", "0").strip().lower() not in ("0", "false", "no", ""):
                v = float(self._last_set_v)
                a = float(self._last_set_i)
            else:
                v, a = self._hal.supply().get_status()
        except Exception:
            v, a = float(self._last_set_v), float(self._last_set_i)
        try:
            sv = float(os.environ.get("EVSE_FAULT_SCALE_V", "1.0"))
            si = float(os.environ.get("EVSE_FAULT_SCALE_I", "1.0"))
            v, a = v * sv, a * si
        except Exception:
            pass
        self.evse_data_context.present_voltage = float(v)
        self.evse_data_context.present_current = float(a)
        return await super().get_evse_present_current(protocol)

    async def is_evse_power_limit_achieved(self) -> bool:  # type: ignore[override]
        """Signal when EV request exceeds instantaneous power cap.

        Uses configured power cap (EVSE_DC_MAX_POWER_W or EVSE_PERIPH_CFG_P_KW) and
        current set voltage to estimate current cap by power and compares against the
        EV's requested current for this tick.
        """
        try:
            ctx = self.get_ev_data_context()  # type: ignore[attr-defined]
            req_i = float(getattr(ctx, "target_current", 0.0) or 0.0)
            v_set = float(getattr(ctx, "target_voltage", 0.0) or self._last_set_v or 0.0)
            # Determine power cap W
            if "EVSE_DC_MAX_POWER_W" in os.environ:
                p_cap_w = float(os.environ.get("EVSE_DC_MAX_POWER_W", "0") or 0.0)
            else:
                p_kw = os.environ.get("EVSE_PERIPH_CFG_P_KW")
                p_cap_w = float(p_kw) * 1000.0 if p_kw else (self._rated_dc_max_voltage_v * self._rated_dc_max_current_a)
            # Convert to current cap by power; respect rated current
            i_cap_power = float(p_cap_w) / max(1.0, v_set if v_set > 0 else (self._last_set_v or 1.0))
            i_cap = min(float(self._rated_dc_max_current_a), i_cap_power)
            # Consider achieved when requested current exceeds cap by >0.5 A
            return bool(req_i > (i_cap + 0.5))
        except Exception:
            return False

    async def send_charging_command(
        self,
        ev_target_voltage: Optional[float],
        ev_target_current: Optional[float],
        is_precharge: bool = False,
        is_session_bpt: bool = False,
    ):
        # Ensure DC path is enabled early enough, especially during PreCharge.
        # Many EVs expect EVSEPresentVoltage to ramp toward EVTargetVoltage before
        # the first PowerDelivery(Start). With contactor bypass (bench), we gate
        # energizing on dc_enable rather than physical AUX.
        try:
            if is_precharge:
                # If an adapter exposes dc_enable, turn on the DC stage.
                getattr(self._hal, "dc_enable", lambda _on: None)(True)
        except Exception:
            pass
        # Enforce simple slew limits to avoid abrupt steps
        max_dv_per_s = float(os.environ.get("EVSE_DC_MAX_DV_PER_S", "50.0"))
        max_di_per_s = float(os.environ.get("EVSE_DC_MAX_DI_PER_S", "100.0"))
        now = time.time()
        dt = max(1e-3, now - self._last_set_ts)
        cur_v, cur_i = self._last_set_v, self._last_set_i
        tgt_v = float(ev_target_voltage or cur_v)
        tgt_i = float(ev_target_current or cur_i)
        dv = tgt_v - cur_v
        di = tgt_i - cur_i
        max_dv = max_dv_per_s * dt
        max_di = max_di_per_s * dt
        if abs(dv) > max_dv:
            tgt_v = cur_v + max_dv * (1 if dv > 0 else -1)
        if abs(di) > max_di:
            tgt_i = cur_i + max_di * (1 if di > 0 else -1)

        # Optional voltage margining
        # - During PreCharge: default no margin; can subtract a small margin via
        #   EVSE_PRECHARGE_V_MARGIN_V (>=0) if your rectifier consistently overshoots.
        # - During CurrentDemand: allow fractional/absolute margin via
        #   EVSE_DC_V_MARGIN_FRAC and EVSE_DC_V_MARGIN_V (both may be negative/zero).
        cmd_v = tgt_v
        try:
            if is_precharge:
                m_pre_v = float(os.environ.get("EVSE_PRECHARGE_V_MARGIN_V", "0.0"))
                if m_pre_v > 0:
                    cmd_v = max(0.0, tgt_v - m_pre_v)
            else:
                m_frac = float(os.environ.get("EVSE_DC_V_MARGIN_FRAC", "0.0"))
                m_off  = float(os.environ.get("EVSE_DC_V_MARGIN_V", "0.0"))
                cmd_v = max(0.0, tgt_v * (1.0 - m_frac) + m_off)
                # Do not intentionally exceed EV's requested target unless explicitly configured
                max_overshoot = float(os.environ.get("EVSE_DC_V_MAX_CMD_OVERSHOOT_V", "0.0"))
                if cmd_v > tgt_v + max(0.0, max_overshoot):
                    cmd_v = tgt_v + max(0.0, max_overshoot)
        except Exception:
            cmd_v = tgt_v

        # Query present measurements for thermal and context updates
        try:
            v_meas, i_meas = self._hal.supply().get_status()
        except Exception:
            v_meas, i_meas = 0.0, 0.0

        # Auto mode selection: decide once based on target voltage hint (prefer CD > PC)
        try:
            v_hint = float(ev_target_voltage or 0.0)
            if self._mode_pref == "low":
                self._mode = "low"
            elif self._mode_pref == "high":
                self._mode = "high"
            else:
                # auto: use hysteresis until locked
                if not self._mode_locked and v_hint > 0.0:
                    if self._mode == "low" and v_hint >= self._hi_enter_v:
                        self._mode = "high"
                    elif self._mode == "high" and v_hint <= self._hi_exit_v:
                        self._mode = "low"
        except Exception:
            pass

        # PreCharge runtime acceleration: if measured voltage is still far from target,
        # push aggressive ramps on the periph (one-shot) and ensure ignore_cp when benching.
        try:
            if is_precharge and not self._precharge_accel_done:
                tgt_v_chk = float(ev_target_voltage or 0.0)
                # Consider acceleration if we're >30V below target and target is sub-HV
                if tgt_v_chk > 0.0 and tgt_v_chk < 600.0 and (tgt_v_chk - float(v_meas)) > 30.0:
                    ramp_v = float(os.environ.get("EVSE_PERIPH_CFG_RAMP_V", "300"))
                    ramp_i = float(os.environ.get("EVSE_PERIPH_CFG_RAMP_I", "80"))
                    cfg = {"ramp_v": ramp_v, "ramp_i": ramp_i}
                    # In bench mode allow periph to ignore CP gating
                    if os.environ.get("EVSE_SIM_CONTACTOR", "0").strip().lower() not in ("0", "false", "no", ""):
                        cfg["ignore_cp"] = True
                    # Apply per-mode v bounds via Hi/Lo thresholds to avoid overshoot
                    if self._mode == "high":
                        cfg.update({"hilo_enter_v": max(430.0, min(self._hi_enter_v, 480.0)),
                                    "hilo_exit_v":  max(400.0, min(self._hi_exit_v,  460.0))})
                    else:
                        # Keep thresholds far so we stay in LOW
                        cfg.update({"hilo_enter_v": 800.0, "hilo_exit_v": 700.0})
                    getattr(self._hal, "periph_cfg", lambda **_: None)(**cfg)
                    self._precharge_accel_done = True
        except Exception:
            pass

        # Overshoot guard: if measured voltage already exceeds EV target by more than
        # EVSE_DC_V_OVERSHOOT_GUARD_V, bias the commanded voltage slightly below the
        # target to help the rectifier settle without ringing.
        try:
            guard_v = float(os.environ.get("EVSE_DC_V_OVERSHOOT_GUARD_V", "0.0"))
        except Exception:
            guard_v = 0.0
        if not is_precharge and guard_v > 0 and v_meas > (tgt_v + guard_v):
            try:
                cmd_v = min(cmd_v, max(0.0, tgt_v - min(guard_v, 5.0)))
            except Exception:
                pass

        # Thermal derating and fault handling
        dec = self._thermal.update(
            rated_current_a=float(self._rated_dc_max_current_a),
            target_voltage_v=float(cmd_v),
            target_current_a=float(tgt_i),
            measured_voltage_v=float(v_meas),
            measured_current_a=float(i_meas),
        )

        # Adjust the current limit applied to hardware
        allowed_i = float(min(tgt_i, dec.allowed_current_a))

        # Update the ISO15118 session limits so EV sees our dynamic capability
        try:
            dc_limits = self.evse_data_context.session_limits.dc_limits
            # Ensure non-negative and don't exceed rated
            dc_limits.max_charge_current = max(0.0, min(self._rated_dc_max_current_a, allowed_i))
        except Exception:
            pass

        # Apply per-mode thresholds during CurrentDemand (only if not locked into a specific setting already)
        try:
            if not is_precharge:
                if self._mode == "high":
                    getattr(self._hal, "periph_cfg", lambda **_: None)(hilo_enter_v=max(430.0, min(self._hi_enter_v, 480.0)),
                                                                         hilo_exit_v=max(400.0, min(self._hi_exit_v, 460.0)))
                else:
                    getattr(self._hal, "periph_cfg", lambda **_: None)(hilo_enter_v=800.0, hilo_exit_v=700.0)
        except Exception:
            pass

        # Apply to hardware
        try:
            self._hal.supply().set_voltage(max(0.0, cmd_v))
            self._hal.supply().set_current_limit(max(0.0, allowed_i))
        except Exception:
            pass

        # Safety: if fault latched, ensure contactor is opened
        if dec.state == "FAULT":
            try:
                self._hal.contactor().set_closed(False)
            except Exception:
                pass
            # Unlock promptly on fault so the user can remove connector
            try:
                if os.environ.get("CABLE_UNLOCK_ON_FAULT", "1").strip().lower() not in ("0", "false", "no"):
                    lock = getattr(self._hal, "cable_lock", None)
                    if callable(lock):
                        lock = lock()
                    if lock:
                        getattr(lock, "unlock", lambda: None)()
            except Exception:
                pass
            # Hint to CP to return to safe state if possible
            try:
                getattr(self._hal, "esp_set_mode", lambda _m=None: None)("manual")
                getattr(self._hal, "esp_set_pwm", lambda _d, enable=True: None)(100, True)
                getattr(self._hal, "esp_set_mode", lambda _m=None: None)("dc")
            except Exception:
                pass

        # Persist the commanded values (post-margin, post-derate) for observability
        self._last_set_v, self._last_set_i, self._last_set_ts = cmd_v, allowed_i, now
        self._last_allowed_i = allowed_i
        # Update context for reporting
        try:
            self.evse_data_context.present_voltage = float(v_meas)
            self.evse_data_context.present_current = float(i_meas)
        except Exception:
            pass
        # Optional tiny settle to allow first CurrentDemand read to reflect movement.
        # Keep within the 250 ms loop budget; default 50 ms.
        try:
            import asyncio as _asyncio
            settle = float(os.environ.get("EVSE_CD_SETTLE_MS", "0.05"))
            if settle > 0:
                await _asyncio.sleep(min(0.1, max(0.0, settle)))
        except Exception:
            pass
        # Log thermal decisions on notable events
        try:
            if dec.state != "OK":
                logger.warning(
                    "Thermal decision",
                    extra={
                        "state": dec.state,
                        "allowed_a": round(dec.allowed_current_a, 2),
                        "hottest": dec.hottest_sensor,
                        "temp_c": dec.hottest_temp_c,
                        "reason": dec.reason,
                    },
                )
        except Exception:
            pass

    async def is_contactor_closed(self) -> Optional[bool]:
        # Allow simulated closure for bring-up when no real contactor/AUX is present
        try:
            sim = os.environ.get("EVSE_SIM_CONTACTOR", "0").strip().lower() not in ("0", "false", "no", "")
            if sim:
                return True
        except Exception:
            pass

        try:
            if self._hal.contactor().is_closed():
                return True
        except Exception:
            # If the HAL doesn't support contactor, simulate success
            return True

        # Proactively issue a close during CableCheck per IEC 61851-23 6.4.3.106
        # Return None on first call to indicate operation in progress; allow a short wait window
        try:
            wait_s = float(os.environ.get("EVSE_CONTACTOR_CLOSE_WAIT_S", "2.0"))
        except Exception:
            wait_s = 2.0
        now = time.time()
        if not self._cc_close_issued:
            try:
                self._hal.contactor().set_closed(True)
            except Exception:
                # If we cannot actuate, simulate success to allow test progress
                return True
            self._cc_close_issued = True
            self._cc_close_ts = now
            return None
        # After issuing, allow some time for AUX to report closed
        if (now - self._cc_close_ts) < max(0.1, wait_s):
            try:
                if self._hal.contactor().is_closed():
                    return True
            except Exception:
                return True
            return None
        # Timed out waiting; report actual status (False triggers failure upstream)
        try:
            return bool(self._hal.contactor().is_closed())
        except Exception:
            return True

    async def is_contactor_opened(self) -> bool:
        try:
            sim = os.environ.get("EVSE_SIM_CONTACTOR", "0").strip().lower() not in ("0", "false", "no", "")
            if sim:
                return True
        except Exception:
            pass
        try:
            return not self._hal.contactor().is_closed()
        except Exception:
            return True

    async def get_cp_state(self) -> CpState:
        # Map HAL CP letter state to ISO 15118 CpState with emergency awareness
        st = (self._hal.cp().get_state() or "B").upper()
        if st == "A":
            return CpState.A1
        if st == "B":
            return CpState.B1
        if st == "C":
            return CpState.C2
        if st == "D":
            return CpState.D2
        if st == "E":
            return CpState.E
        if st == "F":
            return CpState.F
        return CpState.UNKNOWN

    async def stop_charger(self) -> None:
        # Bench mode: skip physical contactor, but disable DC output
        try:
            if os.environ.get("EVSE_SIM_CONTACTOR", "0").strip().lower() not in ("0", "false", "no", ""):
                try:
                    getattr(self._hal, "dc_enable", lambda _on: None)(False)
                except Exception:
                    pass
                # Reset CableCheck internal state
                self._cc_close_issued = False
                self._cc_close_ts = 0.0
                return
        except Exception:
            pass
        # Open contactor to cut DC power immediately
        try:
            self._hal.contactor().set_closed(False)
        except Exception:
            pass
        # Reset CableCheck internal state
        self._cc_close_issued = False
        self._cc_close_ts = 0.0
        # Unlock connector promptly on stop/fault to let user remove plug
        try:
            if os.environ.get("CABLE_UNLOCK_ON_FAULT", "1").strip().lower() not in ("0", "false", "no"):
                lock = getattr(self._hal, "cable_lock", None)
                if callable(lock):
                    lock = lock()
                if lock:
                    getattr(lock, "unlock", lambda: None)()
        except Exception:
            pass
        # Best-effort pilot-line based shutdown: drive CP to a safe state
        # Prefer firmware-native controls if available (ESP adapter exposes esp_set_mode/esp_set_pwm)
        try:
            getattr(self._hal, "esp_set_mode", lambda _m=None: None)("manual")
            getattr(self._hal, "esp_set_pwm", lambda _d, enable=True: None)(100, True)
            # Attempt to return to dc mode; ignore failures
            getattr(self._hal, "esp_set_mode", lambda _m=None: None)("dc")
        except Exception:
            # Fallback: try generic PWM interface (may be ignored in dc mode)
            try:
                self._hal.pwm().set_duty(100.0)
            except Exception:
                pass

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
        evse_snapshot = None
        # Derive a concise state name for easier filtering (e.g., CableCheck, PreCharge, CurrentDemand)
        try:
            state_name = getattr(state, "__class__", type(state)).__name__
        except Exception:
            state_name = str(state)
        if ctx is not None:
            try:
                dc_limits = getattr(getattr(ctx, "session_limits", None), "dc_limits", None)
                max_curr = getattr(dc_limits, "max_charge_current", None) if dc_limits else None
                max_volt = getattr(dc_limits, "max_voltage", None) if dc_limits else None
                snapshot = {
                    "present_soc": getattr(ctx, "present_soc", None),
                    "present_voltage": getattr(ctx, "present_voltage", None),
                    "target_voltage": getattr(ctx, "target_voltage", None),
                    "target_current": getattr(ctx, "target_current", None),
                    # Session DC limits (if available)
                    "max_current_limit": max_curr,
                    "max_charge_current": max_curr,
                    "max_voltage": max_volt,
                    "evcc_id": getattr(ctx, "evcc_id", None),
                }
            except Exception:
                snapshot = None
        # Also emit the EVSE side snapshot (measured and last commanded)
        try:
            evse_snapshot = {
                "present_voltage": getattr(self.evse_data_context, "present_voltage", None),
                "present_current": getattr(self.evse_data_context, "present_current", None),
                "set_voltage": round(float(self._last_set_v), 3),
                "set_current": round(float(self._last_set_i), 3),
                "rated_max_current": round(float(self._rated_dc_max_current_a), 3),
                "rated_max_voltage": round(float(self._rated_dc_max_voltage_v), 3),
            }
        except Exception:
            evse_snapshot = None
        # Decide whether the BMS snapshot is meaningful. Early phases often carry
        # placeholder zeros (e.g., 0.0 targets, null SoC) which confuse operators.
        def _bms_valid(s: dict | None) -> bool:
            if not s:
                return False
            try:
                soc = s.get("present_soc")
                tv = s.get("target_voltage")
                ti = s.get("target_current")
                # Consider it valid once SoC is known or a non-zero target is present
                if soc is not None:
                    return True
                if isinstance(tv, (int, float)) and tv > 0:
                    return True
                if isinstance(ti, (int, float)) and ti > 0:
                    return True
            except Exception:
                pass
            return False

        emit_bms = snapshot if _bms_valid(snapshot) else None
        if emit_bms:
            self._last_bms_snapshot = emit_bms
        # Always cache EVSE snapshot if available
        if evse_snapshot:
            self._last_evse_snapshot = evse_snapshot

        # Emit log without placeholder BMS to avoid showing misleading zeros.
        # When BMS is not yet valid, omit the key entirely.
        extra = {
            "state": str(state),
            "iso_state": state_name,
        }
        if emit_bms:
            extra["bms"] = emit_bms
        if evse_snapshot:
            extra["evse"] = evse_snapshot
        logger.info("ISO15118 state", extra=extra)
        # Publish to HLC manager if available
        try:
            from src.hlc.manager import hlc

            hlc.set_protocol_state(state)
        except Exception:
            pass
