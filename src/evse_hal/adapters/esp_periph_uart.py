from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from ..interfaces import (
    CPReader,
    ContactorDriver,
    DCPowerSupply,
    EVSEHardware,
    Meter,
    PWMController,
)
from ..esp_periph_client import EspPeriphClient, MeterSample
from ..lock import CableLockSim


logger = logging.getLogger("hal.esp.periph")


class _ContactorPeriph(ContactorDriver):
    def __init__(self, client: EspPeriphClient) -> None:
        self._c = client
        self._last_ok: Optional[bool] = None
        self._last_aux: Optional[bool] = None
        self._last_ts: float = 0.0

    def set_closed(self, closed: bool) -> None:
        try:
            res = self._c.contactor_set(bool(closed))
            self._last_ok = bool(res.get("ok", False))
            self._last_aux = bool(res.get("aux_ok", False))
            self._last_ts = time.time()
            if closed and not self._last_aux:
                logger.warning("Contactor aux mismatch; forced open", extra={"res": res})
        except Exception as e:
            logger.error("Contactor set failed", extra={"closed": closed, "error": str(e)})
            raise

    def is_closed(self) -> bool:
        try:
            res = self._c.contactor_check()
            commanded = bool(res.get("commanded", False))
            aux_ok = bool(res.get("aux_ok", False))
            self._last_ok = aux_ok if commanded else False
            self._last_aux = aux_ok
            self._last_ts = time.time()
            return bool(commanded and aux_ok)
        except Exception:
            # Fall back to last known if recent
            if (time.time() - self._last_ts) < 2.0 and self._last_ok is not None and self._last_aux is not None:
                return bool(self._last_ok and self._last_aux)
            return False


class _MeterPeriph(Meter):
    def __init__(self, client: EspPeriphClient) -> None:
        self._c = client
        self._last: Optional[MeterSample] = None
        self._t0 = time.time()
        # Keep a simple EMA for avg voltage/current
        self._avg_v = 0.0
        self._avg_i = 0.0
        self._ema_alpha = 0.2

        def _evt(name: str, payload):
            if name == "evt:meter.tick":
                try:
                    v = float(payload.get("v", 0.0))
                    i = float(payload.get("i", 0.0))
                    p = float(payload.get("p", 0.0))
                    e = float(payload.get("e", 0.0))
                    self._update(MeterSample(v, i, p, e))
                except Exception:
                    pass

        self._c.on_event(_evt)
        # Best-effort: start meter stream if supported
        try:
            self._c.send_req("meter.stream_start", {"period_ms": 1000}, timeout=0.5)
        except Exception:
            pass

    def _update(self, m: MeterSample) -> None:
        self._last = m
        # EMA for smoother averages
        self._avg_v = self._ema_alpha * m.voltage_v + (1.0 - self._ema_alpha) * self._avg_v
        self._avg_i = self._ema_alpha * m.current_a + (1.0 - self._ema_alpha) * self._avg_i

    def update(self, voltage_v: float, current_a: float) -> None:
        # Not used; values sourced from ESP. Keep EMA in sync if invoked.
        self._avg_v = self._ema_alpha * voltage_v + (1.0 - self._ema_alpha) * self._avg_v
        self._avg_i = self._ema_alpha * current_a + (1.0 - self._ema_alpha) * self._avg_i

    def _ensure_last(self) -> MeterSample:
        if self._last is None:
            try:
                self._update(self._c.meter_read())
            except Exception:
                self._last = MeterSample(0.0, 0.0, 0.0, 0.0)
        assert self._last is not None
        return self._last

    def get_energy_Wh(self) -> float:
        m = self._ensure_last()
        return float(m.energy_kwh * 1000.0)

    def get_avg_voltage(self) -> float:
        self._ensure_last()
        return float(self._avg_v)

    def get_avg_current(self) -> float:
        self._ensure_last()
        return float(self._avg_i)

    def get_session_time_s(self) -> float:
        return float(time.time() - self._t0)

    def reset(self) -> None:
        self._t0 = time.time()
        self._last = None
        self._avg_v = 0.0
        self._avg_i = 0.0


class _SupplySim(DCPowerSupply):
    """Placeholder DC supply that uses the existing simulator backend.

    The ESP peripheral can be extended in the future to accept voltage/current
    limits and report status; for now we reuse the Sim implementation to keep
    the orchestrator flow intact.
    """

    def __init__(self, fallback: DCPowerSupply) -> None:
        self._impl = fallback

    def set_voltage(self, volts: float) -> None:
        self._impl.set_voltage(volts)

    def set_current_limit(self, amps: float) -> None:
        self._impl.set_current_limit(amps)

    def get_status(self) -> Tuple[float, float]:
        return self._impl.get_status()


class _SupplyFromMeter(DCPowerSupply):
    """Supply proxy reporting measured V/I from ESP meter only.

    - Avoids generating any simulated telemetry on the host.
    - set_* calls are accepted and cached for observability.
    - get_status() returns meter averages or single-shot reads.
    """

    def __init__(self, client: EspPeriphClient, meter: "_MeterPeriph") -> None:
        self._c = client
        self._meter = meter
        self._last_set_v = 0.0
        self._last_set_i = 0.0

    def set_voltage(self, volts: float) -> None:
        self._last_set_v = float(max(0.0, volts))

    def set_current_limit(self, amps: float) -> None:
        self._last_set_i = float(max(0.0, amps))

    def get_status(self) -> Tuple[float, float]:
        try:
            v = float(self._meter.get_avg_voltage())
            i = float(self._meter.get_avg_current())
            return v, i
        except Exception:
            try:
                m = self._c.meter_read()
                return float(m.voltage_v), float(m.current_a)
            except Exception:
                return 0.0, 0.0


class _EspPWM(PWMController):
    def __init__(self, c: EspPeriphClient) -> None:
        self._c = c

    def set_duty(self, duty_percent: float) -> None:
        st = self._c.cp_get_status(wait_s=0.1)
        mode = getattr(st, "mode", None) if st else None
        logger.info("HAL PWM set_duty", extra={"duty_percent": duty_percent, "mode": mode})
        if mode != "manual":
            return
        try:
            self._c.cp_set_pwm(int(duty_percent), enable=True)
        except Exception as e:
            logger.warning("HAL PWM set_duty failed", extra={"error": str(e)})


class _EspCP(CPReader):
    def __init__(self, c: EspPeriphClient) -> None:
        self._c = c
        self._last_state: Optional[str] = None
        self._raw_state: Optional[str] = None
        self._raw_since: float = 0.0
        self._debounced_state: Optional[str] = None
        self._debounced_since: float = 0.0
        try:
            self._debounce_s: float = float(os.environ.get("CP_DEBOUNCE_S", "0.05"))
        except Exception:
            self._debounce_s = 0.05

    def read_voltage(self) -> float:
        st = self._c.cp_get_status(wait_s=0.2)
        if st:
            self._update_states_from_status(st)
            return st.cp_mv / 1000.0
        return 0.0

    def simulate_state(self, state: str) -> None:
        self._last_state = state

    def get_state(self) -> Optional[str]:
        st = self._c.cp_get_status(wait_s=0.05)
        if st:
            self._update_states_from_status(st)
        return self._debounced_state or self._last_state

    def _update_states_from_status(self, st) -> None:
        now = time.time()
        raw = (st.state or "").strip().upper()[:1] or None
        if raw != self._raw_state:
            self._raw_state = raw
            self._raw_since = now
        if self._debounced_state is None and raw is not None:
            self._debounced_state = raw
            self._debounced_since = now
            self._last_state = raw
            logger.info("CP state (init)", extra={"state": raw})
            return
        if raw in ("E", "F") and raw != self._debounced_state:
            prev = self._debounced_state
            self._debounced_state = raw
            self._debounced_since = now
            self._last_state = raw
            logger.warning("CP emergency state", extra={"from": prev, "to": raw, "cp_mv": st.cp_mv})
            return
        if raw is not None and raw != self._debounced_state:
            stable = max(0.0, now - self._raw_since)
            if stable >= max(0.0, self._debounce_s):
                prev = self._debounced_state
                self._debounced_state = raw
                self._debounced_since = now
                self._last_state = raw
                logger.info("CP state", extra={"from": prev, "to": raw, "stable_ms": int(stable * 1000)})
        else:
            self._last_state = self._debounced_state or raw


@dataclass
class ESPPeriphHardware(EVSEHardware):
    _periph: EspPeriphClient
    _cp_client: Optional[EspPeriphClient]
    _pwm: PWMController
    _cp: CPReader
    _cont: ContactorDriver
    _meter: Meter
    _sup: DCPowerSupply
    _lock: CableLockSim

    def __init__(self, periph_port: Optional[str] = None, cp_port: Optional[str] = None) -> None:
        # Single ESP device with unified UART
        port = periph_port or os.environ.get("ESP_PERIPH_PORT") or os.environ.get("ESP_CP_PORT")
        self._periph = EspPeriphClient(port=port)
        self._periph.connect()
        try:
            info = self._periph.sys_info(timeout=1.0)
            logger.info("ESP periph info", extra={"mode": info.get("mode"), "caps": info.get("capabilities")})
        except Exception as e:
            logger.warning("ESP periph info failed", extra={"error": str(e)})
        # Wire interfaces
        # Configure CP to dc mode; ignore failures
        try:
            self._periph.cp_set_mode("dc")
        except Exception:
            pass
        self._pwm = _EspPWM(self._periph)
        self._cp = _EspCP(self._periph)

        self._cont = _ContactorPeriph(self._periph)
        self._meter = _MeterPeriph(self._periph)
        self._sup = _SupplyFromMeter(self._periph, self._meter)
        self._lock = CableLockSim()

    def pwm(self) -> PWMController:
        return self._pwm

    def cp(self) -> CPReader:
        return self._cp

    def contactor(self) -> ContactorDriver:
        return self._cont

    def supply(self) -> DCPowerSupply:
        return self._sup

    def meter(self) -> Meter:
        return self._meter

    # Diagnostics for bring-up
    def periph_ping(self, timeout: float = 0.5) -> bool:
        try:
            self._periph.sys_ping(timeout=timeout)
            return True
        except Exception:
            return False

    def periph_set_mode(self, mode: str) -> None:
        self._periph.sys_set_mode(mode)

    def cable_lock(self) -> CableLockSim:
        return self._lock

    # Compatibility helpers (parity with esp-uart adapter)
    def restart_slac_hint(self, reset_ms: int = 400) -> None:
        try:
            self._periph.cp_restart_slac_hint(reset_ms)
        except Exception:
            pass

    def esp_ping(self, timeout: float = 0.5) -> bool:
        try:
            return self._periph.cp_ping(timeout)
        except Exception:
            return False

    def esp_set_mode(self, mode: str) -> None:
        try:
            self._periph.cp_set_mode(mode)
        except Exception:
            pass

    def esp_set_pwm(self, duty: int, enable: bool = True) -> None:
        try:
            self._periph.cp_set_pwm(int(duty), enable=enable)
        except Exception:
            pass
