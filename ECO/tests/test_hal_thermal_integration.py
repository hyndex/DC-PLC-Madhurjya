import asyncio
import os

from src.evse_hal.adapters.sim import SimHardware
from src.evse_hal.iso15118_hal_controller import HalEVSEController


async def _run_once(ctrl: HalEVSEController, v: float, i: float):
    await ctrl.send_charging_command(ev_target_voltage=v, ev_target_current=i)


def test_hal_controller_derates_via_session_limits(monkeypatch):
    # Set rated max via env and prime controller
    monkeypatch.setenv("EVSE_DC_MAX_CURRENT_A", "200")
    # Connector derate between 70C and 90C
    monkeypatch.setenv("EVSE_THERMAL_WARN_CONNECTOR_C", "70")
    monkeypatch.setenv("EVSE_THERMAL_SHUTDOWN_CONNECTOR_C", "90")
    # Simulate connector temperature mid-derate
    monkeypatch.setenv("EVSE_THERMAL_SENSOR_CONNECTOR_C", "80")
    # Disable slew limiting for the test (jump to target immediately)
    monkeypatch.setenv("EVSE_DC_MAX_DI_PER_S", "100000")

    hal = SimHardware()
    ctrl = HalEVSEController(hal)

    # Prime rated limits
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(ctrl.get_dc_charge_parameters())
        # Request 200 A at 400 V
        loop.run_until_complete(_run_once(ctrl, 400.0, 200.0))
    finally:
        loop.close()

    # Session limits should be derated vs rated (200A)
    max_i = ctrl.evse_data_context.session_limits.dc_limits.max_charge_current
    assert max_i is not None and 1.0 <= max_i < 180.0


def test_hal_fault_opens_contactor(monkeypatch):
    # Force shutdown on connector and verify contactor opens
    monkeypatch.setenv("EVSE_THERMAL_SHUTDOWN_CONNECTOR_C", "70")
    monkeypatch.setenv("EVSE_THERMAL_SENSOR_CONNECTOR_C", "80")
    monkeypatch.setenv("EVSE_DC_MAX_DI_PER_S", "100000")
    hal = SimHardware()
    # Pre-close contactor
    hal.contactor().set_closed(True)
    ctrl = HalEVSEController(hal)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(ctrl.get_dc_charge_parameters())
        loop.run_until_complete(_run_once(ctrl, 400.0, 100.0))
    finally:
        loop.close()
    assert hal.contactor().is_closed() is False
