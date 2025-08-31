from __future__ import annotations

import os
from dataclasses import dataclass
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
