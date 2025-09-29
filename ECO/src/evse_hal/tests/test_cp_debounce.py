import os
import time
import pytest

from src.evse_hal.adapters.esp_uart import _EspCP
from src.evse_hal.esp_cp_client import CPStatus, PWMStatus


class _FakeClient:
    def __init__(self, frames):
        # frames: list of tuples (state_letter, cp_mv)
        self._frames = list(frames)

    def get_status(self, wait_s: float = 0.0):
        if not self._frames:
            return None
        st, mv = self._frames.pop(0)
        return CPStatus(
            cp_mv=int(mv * 1000),
            state=st,
            pwm=PWMStatus(enabled=True, duty=5, hz=1000),
            ts=time.time(),
            mode="dc",
            cp_mv_robust=int(mv * 1000),
        )


@pytest.mark.parametrize("debounce_s", [0.05, 0.02])
def test_cp_debounce_ignores_short_glitch(monkeypatch, debounce_s):
    monkeypatch.setenv("CP_DEBOUNCE_S", str(debounce_s))
    # Start in C, then brief B shorter than debounce, then back to C
    frames = [
        ("C", 6.0),
        ("B", 9.0),
        ("C", 6.0),
    ]
    cp = _EspCP(_FakeClient(frames))

    # Initial state should lock to C
    s1 = cp.get_state()
    assert s1 == "C"

    # Provide a very short time to emulate a glitch shorter than debounce
    time.sleep(max(0.0, debounce_s / 2.0))
    s2 = cp.get_state()
    # Should remain C due to debounce
    assert s2 == "C"

    # After another read with the back-to-C frame, still C
    s3 = cp.get_state()
    assert s3 == "C"


def test_cp_debounce_emergency_immediate(monkeypatch):
    monkeypatch.setenv("CP_DEBOUNCE_S", "0.5")
    frames = [("C", 6.0), ("E", 0.0)]
    cp = _EspCP(_FakeClient(frames))
    assert cp.get_state() == "C"
    # Next frame is E; emergency should bypass debounce
    s2 = cp.get_state()
    assert s2 == "E"

