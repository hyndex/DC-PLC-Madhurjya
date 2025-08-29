from __future__ import annotations

import os
from dataclasses import dataclass
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


class _EspPWM(PWMController):
    def __init__(self, client: EspCpClient) -> None:
        self._c = client

    def set_duty(self, duty_percent: float) -> None:
        self._c.set_pwm(int(duty_percent), enable=True)


class _EspCP(CPReader):
    def __init__(self, client: EspCpClient) -> None:
        self._c = client
        self._last_state: Optional[str] = None

    def read_voltage(self) -> float:
        st = self._c.get_status(wait_s=0.2)
        if st:
            self._last_state = st.state
            return st.cp_mv / 1000.0
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
        except Exception:
            pass
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
