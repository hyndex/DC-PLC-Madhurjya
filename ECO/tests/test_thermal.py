import os
import time

from src.evse_hal.thermal import ThermalManager, ThermalReading


def make_sensor(name: str, c: float) -> ThermalReading:
    return ThermalReading(name=name, temp_c=c, ts=time.time())


def test_derating_linear_between_thresholds(monkeypatch):
    # Configure clear thresholds for connector
    monkeypatch.setenv("EVSE_THERMAL_WARN_CONNECTOR_C", "70")
    monkeypatch.setenv("EVSE_THERMAL_SHUTDOWN_CONNECTOR_C", "90")
    tm = ThermalManager()

    rated = 200.0
    tstart = 70.0
    tend = 90.0
    # Midway should yield ~50% allowed current
    mid = (tstart + tend) / 2
    dec = tm.update(
        rated_current_a=rated,
        target_voltage_v=400.0,
        target_current_a=200.0,
        measured_voltage_v=400.0,
        measured_current_a=150.0,
        extra_sensors={"CONNECTOR": make_sensor("CONNECTOR", mid)},
    )
    assert dec.state == "DERATE"
    assert 0.45 * rated <= dec.allowed_current_a <= 0.55 * rated


def test_fault_latch_and_cooldown(monkeypatch):
    # Faster cooldown for test
    monkeypatch.setenv("EVSE_THERMAL_FAULT_HOLD_S", "0.05")
    monkeypatch.setenv("EVSE_THERMAL_COOLDOWN_C", "40")
    tm = ThermalManager()
    rated = 100.0

    # Over shutdown -> fault
    dec = tm.update(
        rated_current_a=rated,
        target_voltage_v=400.0,
        target_current_a=100.0,
        measured_voltage_v=390.0,
        measured_current_a=90.0,
        extra_sensors={"RECTIFIER": make_sensor("RECTIFIER", 110.0)},
    )
    assert dec.state == "FAULT"
    assert dec.allowed_current_a == 0.0

    # Drop below cooldown and hold -> fault clears
    dec = tm.update(
        rated_current_a=rated,
        target_voltage_v=400.0,
        target_current_a=100.0,
        measured_voltage_v=400.0,
        measured_current_a=0.0,
        extra_sensors={"RECTIFIER": make_sensor("RECTIFIER", 35.0)},
    )
    # Immediately still latched
    assert tm.last_decision().state == "FAULT"
    time.sleep(0.06)
    dec2 = tm.update(
        rated_current_a=rated,
        target_voltage_v=400.0,
        target_current_a=100.0,
        measured_voltage_v=400.0,
        measured_current_a=0.0,
        extra_sensors={"RECTIFIER": make_sensor("RECTIFIER", 35.0)},
    )
    assert dec2.state in ("OK", "DERATE")


def test_voltage_sag_derate(monkeypatch):
    monkeypatch.setenv("EVSE_THERMAL_ENABLE_SAG", "1")
    monkeypatch.setenv("EVSE_THERMAL_SAG_FRAC", "0.08")
    monkeypatch.setenv("EVSE_THERMAL_SAG_MIN_A", "10")
    monkeypatch.setenv("EVSE_THERMAL_SAG_DERATE", "0.5")
    tm = ThermalManager()
    rated = 200.0
    # 10% sag at high current should trigger 50% derate
    dec = tm.update(
        rated_current_a=rated,
        target_voltage_v=100.0,
        target_current_a=150.0,
        measured_voltage_v=90.0,
        measured_current_a=120.0,
    )
    # Allowed current should be around half of request/rated
    assert 60.0 <= dec.allowed_current_a <= 90.0


def test_multi_sensor_and_fault(monkeypatch):
    # Cable overheats -> fault, even if others are OK
    tm = ThermalManager()
    dec = tm.update(
        rated_current_a=150.0,
        target_voltage_v=400.0,
        target_current_a=150.0,
        measured_voltage_v=400.0,
        measured_current_a=100.0,
        extra_sensors={
            "CABLE": make_sensor("CABLE", 110.0),
            "RECTIFIER": make_sensor("RECTIFIER", 80.0),
            "CONNECTOR": make_sensor("CONNECTOR", 60.0),
        },
    )
    assert dec.state == "FAULT"
    assert dec.allowed_current_a == 0.0


def test_ok_when_below_warn_no_sag(monkeypatch):
    tm = ThermalManager()
    dec = tm.update(
        rated_current_a=100.0,
        target_voltage_v=400.0,
        target_current_a=80.0,
        measured_voltage_v=400.0,
        measured_current_a=10.0,
        extra_sensors={"CONNECTOR": make_sensor("CONNECTOR", 25.0)},
    )
    assert dec.state == "OK"
    assert abs(dec.allowed_current_a - 80.0) < 1e-6


def test_derate_window_override(monkeypatch):
    monkeypatch.setenv("EVSE_THERMAL_DERATE_START_CONNECTOR_C", "50")
    monkeypatch.setenv("EVSE_THERMAL_DERATE_END_CONNECTOR_C", "60")
    tm = ThermalManager()
    dec = tm.update(
        rated_current_a=100.0,
        target_voltage_v=400.0,
        target_current_a=100.0,
        measured_voltage_v=400.0,
        measured_current_a=50.0,
        extra_sensors={"CONNECTOR": make_sensor("CONNECTOR", 55.0)},
    )
    # exactly mid-window -> ~50% allowed
    assert 45.0 <= dec.allowed_current_a <= 55.0


def test_combined_sag_and_temp_derate(monkeypatch):
    monkeypatch.setenv("EVSE_THERMAL_ENABLE_SAG", "1")
    tm = ThermalManager()
    # Temp derate to ~50%, sag derate also to 50%; algorithm picks stricter (min), not multiplicative
    dec = tm.update(
        rated_current_a=200.0,
        target_voltage_v=100.0,
        target_current_a=200.0,
        measured_voltage_v=90.0,   # 10% sag
        measured_current_a=120.0,  # above sag threshold default 50A
        extra_sensors={"CONNECTOR": make_sensor("CONNECTOR", tm.cfg.warn_c["CONNECTOR"] + (tm.cfg.shutdown_c["CONNECTOR"] - tm.cfg.warn_c["CONNECTOR"]) / 2)},
    )
    assert 90.0 <= dec.allowed_current_a <= 110.0


def test_nonpositive_target_current(monkeypatch):
    tm = ThermalManager()
    dec = tm.update(
        rated_current_a=100.0,
        target_voltage_v=400.0,
        target_current_a=0.0,
        measured_voltage_v=400.0,
        measured_current_a=0.0,
    )
    assert dec.allowed_current_a == 0.0
