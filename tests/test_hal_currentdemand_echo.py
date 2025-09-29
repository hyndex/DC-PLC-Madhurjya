import os
import asyncio

from typing import Optional, Tuple

from src.evse_hal.iso15118_hal_controller import HalEVSEController


class _StubSupply:
    def __init__(self) -> None:
        self.v = 0.0
        self.i = 0.0

    def set_voltage(self, volts: float) -> None:
        self.v = float(volts)

    def set_current_limit(self, amps: float) -> None:
        self.i = float(amps)

    def get_status(self) -> Tuple[float, float]:
        return self.v, self.i


class _StubCP:
    def __init__(self) -> None:
        self._state = "C"

    def read_voltage(self) -> float:
        return 0.0

    def simulate_state(self, state: str) -> None:
        self._state = state

    def get_state(self) -> Optional[str]:
        return self._state


class _StubContactor:
    def __init__(self) -> None:
        self._closed = False

    def set_closed(self, closed: bool) -> None:
        self._closed = bool(closed)

    def is_closed(self) -> bool:
        return self._closed


class _StubMeter:
    def __init__(self) -> None:
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


class _StubHAL:
    def __init__(self) -> None:
        self._sup = _StubSupply()
        self._cp = _StubCP()
        self._cont = _StubContactor()
        self._meter = _StubMeter()

    def pwm(self):
        raise NotImplementedError

    def cp(self):
        return self._cp

    def contactor(self):
        return self._cont

    def supply(self):
        return self._sup

    def meter(self):
        return self._meter

    def close(self) -> None:
        pass


def _setenv(mapping):
    for k, v in mapping.items():
        os.environ[str(k)] = str(v)


async def _mk_controller():
    return HalEVSEController(_StubHAL())


def test_echo_shaping_and_caps():
    # Configure echo and limits
    _setenv(
        {
            "EVSE_ECHO_CURRENTDEMAND": "1",
            "EVSE_ECHO_I_FLOOR_A": "1.0",
            "EVSE_ECHO_I_FLOOR_FRAC": "0.05",
            "EVSE_ECHO_I_MAX_A": "200",
            "EVSE_ECHO_P_MAX_W": "30000",
            "EVSE_PERIPH_CFG_V_MIN": "150",
            "EVSE_PERIPH_CFG_V_MAX": "1000",
            "EVSE_FAST_HARD_APPLY": "1",
        }
    )
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        ctrl = loop.run_until_complete(_mk_controller())
        # Command something that would be above P limit at low V
        v_req = 300.0
        i_req = 150.0  # 300V * 150A = 45 kW, exceeds 30 kW cap
        loop.run_until_complete(
            ctrl.send_charging_command(ev_target_voltage=v_req, ev_target_current=i_req)
        )
        # Populate evse_data_context with present values (protocol arg unused for context update)
        loop.run_until_complete(ctrl.get_evse_present_voltage(None))
        loop.run_until_complete(ctrl.get_evse_present_current(None))
        v_present = float(ctrl.evse_data_context.present_voltage)
        i_present = float(ctrl.evse_data_context.present_current)
        # Expected: voltage echoes request within v_min/v_max
        assert 299.9 <= v_present <= 300.1
        # Expected: current capped by Pmax/V => 30000/300 = 100 A (also within Imax=200)
        assert 99.0 <= i_present <= 101.0
    finally:
        loop.close()


def test_cp_sticky_current_demand():
    _setenv({"EVSE_CP_STICKY_MS": "600"})
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        ctrl = loop.run_until_complete(_mk_controller())
        # Enter CurrentDemand (activates sticky window)
        CurrentDemandDummy = type("CurrentDemand", (), {})
        loop.run_until_complete(ctrl.set_present_protocol_state(CurrentDemandDummy()))
        # Simulate CP dip to B; expect get_cp_state still returns C/D indicator under sticky window
        hal = ctrl._hal  # type: ignore[attr-defined]
        hal.cp().simulate_state("B")
        st = loop.run_until_complete(ctrl.get_cp_state())
        # Allowed outputs are C2 or D2; sticky default memory starts at 'C'
        assert str(st).startswith("C") or str(st).startswith("D")
    finally:
        loop.close()
