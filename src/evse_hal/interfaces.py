from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Protocol, Tuple


@dataclass
class MeterReading:
    voltage_v: float
    current_a: float
    energy_Wh: float


class PWMController(ABC):
    @abstractmethod
    def set_duty(self, duty_percent: float) -> None:
        ...


class CPReader(ABC):
    @abstractmethod
    def read_voltage(self) -> float:
        ...

    @abstractmethod
    def simulate_state(self, state: str) -> None:
        ...

    @abstractmethod
    def get_state(self) -> Optional[str]:
        ...


class ContactorDriver(ABC):
    @abstractmethod
    def set_closed(self, closed: bool) -> None:
        ...

    @abstractmethod
    def is_closed(self) -> bool:
        ...


class CableLockDriver(ABC):
    @abstractmethod
    def lock(self) -> None:
        ...

    @abstractmethod
    def unlock(self) -> None:
        ...


class DCPowerSupply(ABC):
    @abstractmethod
    def set_voltage(self, volts: float) -> None:
        ...

    @abstractmethod
    def set_current_limit(self, amps: float) -> None:
        ...

    @abstractmethod
    def get_status(self) -> Tuple[float, float]:
        ...


class Meter(ABC):
    @abstractmethod
    def update(self, voltage_v: float, current_a: float) -> None:
        ...

    @abstractmethod
    def get_energy_Wh(self) -> float:
        ...

    @abstractmethod
    def get_avg_voltage(self) -> float:
        ...

    @abstractmethod
    def get_avg_current(self) -> float:
        ...

    @abstractmethod
    def get_session_time_s(self) -> float:
        ...

    @abstractmethod
    def reset(self) -> None:
        ...


class EVSEHardware(ABC):
    @abstractmethod
    def pwm(self) -> PWMController:
        ...

    @abstractmethod
    def cp(self) -> CPReader:
        ...

    @abstractmethod
    def contactor(self) -> ContactorDriver:
        ...

    @abstractmethod
    def supply(self) -> DCPowerSupply:
        ...

    @abstractmethod
    def meter(self) -> Meter:
        ...
