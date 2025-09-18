import sys
import types

import pytest

# Provide lightweight stubs for the iso15118 enums used by the controller when the
# full package is not installed (common during unit testing).
if 'iso15118.shared.messages.enums' not in sys.modules:
    iso_shared = types.ModuleType('iso15118.shared')
    iso_shared_messages = types.ModuleType('iso15118.shared.messages')
    iso_shared_enums = types.ModuleType('iso15118.shared.messages.enums')

    class _CpState:
        A1 = 'A1'
        B1 = 'B1'
        C2 = 'C2'
        D2 = 'D2'
        E = 'E'
        F = 'F'
        UNKNOWN = 'UNKNOWN'

    iso_shared_enums.CpState = _CpState
    sys.modules['iso15118.shared'] = iso_shared
    sys.modules['iso15118.shared.messages'] = iso_shared_messages
    sys.modules['iso15118.shared.messages.enums'] = iso_shared_enums

from src.evse_hal.iso15118_hal_controller import HalEVSEController
from src.evse_hal.interfaces import EVSEHardware, CPReader, PWMController, ContactorDriver, DCPowerSupply, Meter
from iso15118.shared.messages.enums import CpState


class _StubCP(CPReader):
    def __init__(self, state: str = "B"):
        self._state = state

    def read_voltage(self) -> float:
        return 9.0

    def simulate_state(self, state: str) -> None:
        self._state = state

    def get_state(self):
        return self._state


class _NoopPWM(PWMController):
    def set_duty(self, duty_percent: float) -> None:
        pass


class _NoopContactor(ContactorDriver):
    def __init__(self):
        self._closed = False

    def set_closed(self, closed: bool) -> None:
        self._closed = bool(closed)

    def is_closed(self) -> bool:
        return self._closed


class _NoopSupply(DCPowerSupply):
    def __init__(self):
        self._v, self._i = 0.0, 0.0

    def set_voltage(self, volts: float) -> None:
        self._v = float(volts)

    def set_current_limit(self, amps: float) -> None:
        self._i = float(amps)

    def get_status(self):
        return self._v, self._i


class _NoopMeter(Meter):
    def __init__(self):
        self._e = 0.0

    def update(self, voltage_v: float, current_a: float) -> None:
        pass

    def get_energy_Wh(self) -> float:
        return self._e

    def get_avg_voltage(self) -> float:
        return 0.0

    def get_avg_current(self) -> float:
        return 0.0

    def get_session_time_s(self) -> float:
        return 0.0

    def reset(self) -> None:
        self._e = 0.0


class _StubHAL(EVSEHardware):
    def __init__(self):
        self._cp = _StubCP("B")
        self._pwm = _NoopPWM()
        self._cont = _NoopContactor()
        self._sup = _NoopSupply()
        self._meter = _NoopMeter()

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

    def close(self) -> None:
        return


@pytest.mark.asyncio
async def test_hal_cp_mapping_basic():
    hal = _StubHAL()
    ctrl = HalEVSEController(hal)

    # B -> B1
    hal.cp().simulate_state("B")
    assert await ctrl.get_cp_state() == CpState.B1

    # C -> C2
    hal.cp().simulate_state("C")
    assert await ctrl.get_cp_state() == CpState.C2

    # D -> D2
    hal.cp().simulate_state("D")
    assert await ctrl.get_cp_state() == CpState.D2

    # E/F -> propagate emergency states
    hal.cp().simulate_state("E")
    assert await ctrl.get_cp_state() == CpState.E
    hal.cp().simulate_state("F")
    assert await ctrl.get_cp_state() == CpState.F
