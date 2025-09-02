from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from src.evse_hal.interfaces import (
    CPReader,
    ContactorDriver,
    DCPowerSupply,
    EVSEHardware,
    Meter,
    PWMController,
)
from src.ccs_sim import pwm as sim_pwm
from src.ccs_sim.precharge import DCPowerSupplySim
from src.ccs_sim.emeter import EnergyMeterSim
from src.evse_hal.lock import CableLockSim


class _SimPWM(PWMController):
    def set_duty(self, duty_percent: float) -> None:
        sim_pwm.set_pwm_duty(duty_percent)


class _SimCP(CPReader):
    def __init__(self) -> None:
        self._state = "A"
    def read_voltage(self) -> float:
        return sim_pwm.read_cp_voltage()

    def simulate_state(self, state: str) -> None:
        self._state = state
        sim_pwm.simulate_cp_state(state)

    def get_state(self) -> str:
        return self._state


class _SimContactor(ContactorDriver):
    def __init__(self) -> None:
        self._closed = False

    def set_closed(self, closed: bool) -> None:
        self._closed = bool(closed)

    def is_closed(self) -> bool:
        return self._closed


class _SimSupply(DCPowerSupply):
    def __init__(self) -> None:
        self._s = DCPowerSupplySim()

    def set_voltage(self, volts: float) -> None:
        self._s.set_voltage(volts)

    def set_current_limit(self, amps: float) -> None:
        self._s.set_current_limit(amps)

    def get_status(self) -> Tuple[float, float]:
        return self._s.get_status()

    # expose internals for orchestrator-friendly use
    @property
    def _impl(self) -> DCPowerSupplySim:
        return self._s


class _SimMeter(Meter):
    def __init__(self) -> None:
        self._m = EnergyMeterSim()

    def update(self, voltage_v: float, current_a: float) -> None:
        self._m.update(voltage_v, current_a)

    def get_energy_Wh(self) -> float:
        return self._m.get_total_energy_wh()

    def get_avg_voltage(self) -> float:
        return self._m.get_average_voltage()

    def get_avg_current(self) -> float:
        return self._m.get_average_current()

    def get_session_time_s(self) -> float:
        return self._m.get_session_time()

    def reset(self) -> None:
        self._m.reset()

    @property
    def _impl(self) -> EnergyMeterSim:
        return self._m


@dataclass
class SimHardware(EVSEHardware):
    _pwm: _SimPWM
    _cp: _SimCP
    _cont: _SimContactor
    _sup: _SimSupply
    _meter: _SimMeter
    _lock: CableLockSim

    def __init__(self) -> None:
        self._pwm = _SimPWM()
        self._cp = _SimCP()
        self._cont = _SimContactor()
        self._sup = _SimSupply()
        self._meter = _SimMeter()
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

    # Optional cable lock API for HAL consumers
    def cable_lock(self) -> CableLockSim:
        return self._lock
