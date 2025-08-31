from __future__ import annotations

import os
from dataclasses import dataclass
import time
import logging
from typing import Optional, Tuple

from src.evse_hal.interfaces import (
    CPReader,
    ContactorDriver,
    DCPowerSupply,
    EVSEHardware,
    Meter,
    PWMController,
)
from src.evse_hal.esp_cp_client import EspCpClient
from src.evse_hal.adapters.sim import SimHardware

logger = logging.getLogger("hal.esp")

class _EspPWM(PWMController):
    def __init__(self, client: EspCpClient) -> None:
        self._c = client

    def set_duty(self, duty_percent: float) -> None:
        # Only meaningful in firmware manual mode; avoid spamming errors in dc mode
        st = self._c.get_status(wait_s=0.1)
        mode = getattr(st, "mode", None)
        logger.info("HAL PWM set_duty", extra={"duty_percent": duty_percent, "mode": mode})
        if mode != "manual":
            # Respect firmware policy in dc mode (fixed 5% / 100%)
            return
        try:
            self._c.set_pwm(int(duty_percent), enable=True)
        except Exception as e:
            logger.warning("HAL PWM set_duty failed", extra={"error": str(e)})


class _EspCP(CPReader):
    def __init__(self, client: EspCpClient) -> None:
        self._c = client
        self._last_state: Optional[str] = None

    def read_voltage(self) -> float:
        st = self._c.get_status(wait_s=0.2)
        if st:
            self._last_state = st.state
            v = st.cp_mv / 1000.0
            logger.debug("HAL CP read", extra={"voltage_v": v, "state": st.state, "mode": getattr(st, "mode", None)})
            return v
        return 0.0

    def simulate_state(self, state: str) -> None:
        # Hardware-backed CP ignores simulations
        self._last_state = state

    def get_state(self) -> Optional[str]:
        st = self._c.get_status(wait_s=0.05)
        if st:
            self._last_state = st.state
        return self._last_state


@dataclass
class ESPSerialHardware(EVSEHardware):
    _client: EspCpClient
    _pwm: _EspPWM
    _cp: _EspCP
    _fallback: SimHardware

    def __init__(self, port: Optional[str] = None) -> None:
        self._client = EspCpClient(port=port or os.environ.get("ESP_CP_PORT"))
        self._client.connect()
        # Duplex check
        try:
            ok = self._client.ping(timeout=0.5)
            logger.info("HAL ESP ping", extra={"ok": ok})
        except Exception:
            logger.warning("HAL ESP ping failed")
        # Ensure firmware is in DC auto mode
        try:
            self._client.set_mode("dc")
            logger.info("HAL ESP set_mode(dc)")
        except Exception:
            logger.warning("HAL ESP set_mode(dc) failed")
        self._pwm = _EspPWM(self._client)
        self._cp = _EspCP(self._client)
        # reuse sim for the rest to keep plumbing simple
        self._fallback = SimHardware()

    def pwm(self) -> PWMController:
        return self._pwm

    def cp(self) -> CPReader:
        return self._cp

    def contactor(self) -> ContactorDriver:
        return self._fallback.contactor()

    def supply(self) -> DCPowerSupply:
        return self._fallback.supply()

    def meter(self) -> Meter:
        return self._fallback.meter()

    # Optional helper: attempt to nudge EV/stack to restart SLAC by toggling CP duty
    def restart_slac_hint(self, reset_ms: int = 400) -> None:
        """Try prompting a fresh SLAC by briefly leaving DC 5% indication.

        Sequence:
        - Switch to manual and drive 100% duty for a short period
        - Return to dc mode (firmware enforces 5% in B/C/D)
        """
        try:
            self._client.set_mode("manual")
            self._client.set_pwm(100, enable=True)
            time.sleep(max(0, reset_ms) / 1000.0)
            self._client.set_mode("dc")
            logger.info("HAL ESP SLAC restart hint sent", extra={"reset_ms": reset_ms})
        except Exception as e:
            logger.warning("HAL ESP SLAC restart hint failed", extra={"error": str(e)})

    # Expose minimal ESP controls for diagnostics
    def esp_ping(self, timeout: float = 0.5) -> bool:
        try:
            return self._client.ping(timeout)
        except Exception:
            return False

    def esp_set_mode(self, mode: str) -> None:
        self._client.set_mode(mode)

    def esp_set_pwm(self, duty: int, enable: bool = True) -> None:
        self._client.set_pwm(int(duty), enable=enable)
