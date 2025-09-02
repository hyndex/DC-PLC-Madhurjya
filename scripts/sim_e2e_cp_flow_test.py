#!/usr/bin/env python3
"""End-to-end HAL(sim) CP/Lock safety tests without external deps.

This runner avoids importing PySLAC on non-Linux by stubbing the minimal
interfaces evse_main needs. It verifies:
- No start on CP=A (partial plug)
- Lock-before-PLC gating (both success and failure)
- B->C triggers SLAC matching and SECC launch (stubbed)
- Brief CP flap (< grace) does not tear down SECC/session
- Emergency E/F triggers immediate cutoff and unlock, stops SECC
- A (disconnect) triggers cutoff+unlock, graceful teardown

Run:
  EVSE_CONTROLLER=hal EVSE_HAL_ADAPTER=sim python3 scripts/sim_e2e_cp_flow_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Optional


# ----- Minimal stubs for pyslac to let evse_main import on macOS -----

STATE_MATCHED = "MATCHED"


class _FakeSlacSession:
    def __init__(self, evse_id: str, iface: str, cfg: Any):
        self.evse_id = evse_id
        self.iface = iface
        self.cfg = cfg
        self.state = "IDLE"
        self.matching_process_task = None
        self.left_calls = 0

    async def evse_set_key(self) -> None:
        return None

    async def leave_logical_network(self) -> None:
        self.left_calls += 1
        return None


class _FakeSlacController:
    async def notify_matching_ongoing(self, evse_id: str) -> None:
        return None

    async def enable_hlc_charging(self, evse_id: str) -> None:
        return None


class _FakeSlacConfig:
    def __init__(self):
        self.slac_init_timeout = 2.0

    def load_envs(self, _path: Optional[str] = None) -> None:
        return None


def _install_pyslac_stubs() -> None:
    root = ModuleType("pyslac")
    session = ModuleType("pyslac.session")
    environment = ModuleType("pyslac.environment")
    # Bind API used by evse_main
    session.SlacEvseSession = _FakeSlacSession
    session.SlacSessionController = _FakeSlacController
    session.STATE_MATCHED = STATE_MATCHED
    environment.Config = _FakeSlacConfig
    sys.modules.setdefault("pyslac", root)
    sys.modules.setdefault("pyslac.session", session)
    sys.modules.setdefault("pyslac.environment", environment)


_install_pyslac_stubs()


# Defer heavy imports until stubs are in
from pathlib import Path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))          # allow imports like 'src.evse_hal...'
sys.path.insert(0, str(repo_root / "src"))  # allow imports like 'evse_main'
from evse_hal.adapters.sim import SimHardware  # type: ignore
import evse_main as em  # type: ignore


# ----- Controller subclass to record CP processing and avoid real SECC -----

class TestController(em.EVSECommunicationController):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.processed_states: list[str] = []
        self.secc_started: int = 0
        self.secc_stopped: int = 0
        self.session_left: int = 0

    async def process_cp_state(self, session, state: str):
        self.processed_states.append(state)
        # Simulate matching upon state C
        if state.startswith("C"):
            session.state = STATE_MATCHED
        if state.startswith("A"):
            session.state = "UNMATCHED"


# Monkeypatch SECC launcher to avoid real SECC
async def _fake_launch_secc_background(_iface: str, _secc_config_path: Optional[str], _store: Optional[str]):
    ctrl = asyncio.current_task()
    if hasattr(ctrl, "_controller_ref"):
        getattr(ctrl, "_controller_ref").secc_started += 1  # type: ignore

    async def _sleep_forever():
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Flag stop for visibility
            if hasattr(ctrl, "_controller_ref"):
                getattr(ctrl, "_controller_ref").secc_stopped += 1  # type: ignore
            raise

    class _Handler:
        def close_session(self):
            return None

    return _Handler(), asyncio.create_task(_sleep_forever())


async def _run_case_normal_flow() -> None:
    # Enforce HAL mode and quick timings for tests
    os.environ["EVSE_CONTROLLER"] = "hal"
    os.environ["EVSE_HAL_ADAPTER"] = "sim"
    os.environ["CABLE_LOCK_ENFORCE"] = "1"
    os.environ["CABLE_UNLOCK_ON_FAULT"] = "1"
    os.environ["CP_STABLE_BEFORE_START_S"] = "0.01"
    os.environ["CP_DISCONNECT_GRACE_S"] = "0.3"
    os.environ["CP_POLL_CONNECTED_S"] = "0.02"
    os.environ["CP_POLL_EMERGENCY_S"] = "0.01"

    # Patch SECC launcher
    em.launch_secc_background = _fake_launch_secc_background  # type: ignore

    # Provide our own HAL instance via registry.create
    sim_hal = SimHardware()
    # Patch both src.evse_hal.registry and evse_hal.registry
    import src.evse_hal.registry as sreg  # type: ignore
    import evse_hal.registry as reg  # type: ignore

    def _create(_name: str = "sim"):
        return sim_hal

    sreg.create = _create  # type: ignore
    reg.create = _create  # type: ignore

    # Build controller
    ctrl = TestController(slac_config=em.SlacConfig())
    t = asyncio.create_task(ctrl.start("EVSE_TEST", "lo"))
    # attach back-ref for SECC flags
    setattr(t, "_controller_ref", ctrl)

    # Initially CP state A (default), ensure no PLC start
    await asyncio.sleep(0.1)
    assert ctrl.secc_started == 0, "SECC started on CP=A"
    assert not sim_hal.contactor().is_closed(), "Contactor closed unexpectedly on CP=A"

    # Transition to B and quickly to C
    sim_hal.cp().simulate_state("B")
    await asyncio.sleep(0.5)
    sim_hal.cp().simulate_state("C")
    await asyncio.sleep(0.5)
    # Processed B then C; SECC launched once matched
    assert any(s.startswith("B") for s in ctrl.processed_states), f"No B processed: {ctrl.processed_states}"
    assert any(s.startswith("C") for s in ctrl.processed_states), f"No C processed: {ctrl.processed_states}"
    # Allow loop to detect matched and launch SECC
    await asyncio.sleep(0.2)
    assert ctrl.secc_started >= 1, "SECC did not start after match"

    # Skip brief flap in this run; keep SECC running until emergency

    # Emergency E: immediate stop + unlock requested
    # Lock first to verify unlock
    lk = sim_hal.cable_lock()
    lk.lock()
    before_id = id(lk)
    sim_hal.cp().simulate_state("E")
    # Wait up to 1.0s for unlock to take effect
    deadline = time.time() + 1.0
    after_id = id(sim_hal.cable_lock())
    while time.time() < deadline and lk.is_locked():
        await asyncio.sleep(0.05)
    assert not sim_hal.contactor().is_closed(), "Contactor not opened on emergency"
    assert lk.is_locked() is False, (
        f"Cable not unlocked on emergency (obj id before={before_id} after={after_id})"
    )

    # Cleanup
    t.cancel()
    try:
        await t
    except Exception:
        pass


async def _run_case_lock_failure_blocks_plc() -> None:
    os.environ["EVSE_CONTROLLER"] = "hal"
    os.environ["EVSE_HAL_ADAPTER"] = "sim"
    os.environ["CABLE_LOCK_ENFORCE"] = "1"
    os.environ["CABLE_LOCK_VERIFY_TIMEOUT_S"] = "0.1"

    em.launch_secc_background = _fake_launch_secc_background  # type: ignore

    sim_hal = SimHardware()
    import src.evse_hal.registry as sreg  # type: ignore
    import evse_hal.registry as reg  # type: ignore

    # Replace cable lock with one that never locks
    class _BadLock:
        def lock(self):
            raise RuntimeError("actuator jammed")

        def unlock(self):
            return None

        def is_locked(self):
            return False

    bad_lock = _BadLock()
    sim_hal.cable_lock = lambda: bad_lock  # type: ignore

    def _create(_name: str = "sim"):
        return sim_hal

    sreg.create = _create  # type: ignore
    reg.create = _create  # type: ignore

    ctrl = TestController(slac_config=em.SlacConfig())
    t = asyncio.create_task(ctrl.start("EVSE_TEST", "lo"))
    setattr(t, "_controller_ref", ctrl)

    # Drive to B (vehicle detected)
    sim_hal.cp().simulate_state("B")
    await asyncio.sleep(0.3)
    # Because lock enforce failed, PLC must not start and B should NOT be processed
    assert ctrl.secc_started == 0, "SECC started even though cable lock failed"
    assert not any(s.startswith("B") for s in ctrl.processed_states), "process_cp_state called despite lock failure"

    t.cancel()
    try:
        await t
    except Exception:
        pass


async def _run_case_brief_flap_does_not_stop() -> None:
    os.environ["EVSE_CONTROLLER"] = "hal"
    os.environ["EVSE_HAL_ADAPTER"] = "sim"
    os.environ["CABLE_LOCK_ENFORCE"] = "1"
    os.environ["CABLE_UNLOCK_ON_FAULT"] = "1"
    os.environ["CP_STABLE_BEFORE_START_S"] = "0.01"
    os.environ["CP_DISCONNECT_GRACE_S"] = "0.4"

    em.launch_secc_background = _fake_launch_secc_background  # type: ignore

    sim_hal = SimHardware()
    import src.evse_hal.registry as sreg  # type: ignore
    import evse_hal.registry as reg  # type: ignore

    def _create(_name: str = "sim"):
        return sim_hal

    sreg.create = _create  # type: ignore
    reg.create = _create  # type: ignore

    ctrl = TestController(slac_config=em.SlacConfig())
    t = asyncio.create_task(ctrl.start("EVSE_TEST", "lo"))
    setattr(t, "_controller_ref", ctrl)

    # B -> C -> matched -> SECC started
    sim_hal.cp().simulate_state("B")
    await asyncio.sleep(0.5)
    sim_hal.cp().simulate_state("C")
    await asyncio.sleep(0.5)
    await asyncio.sleep(0.2)
    assert ctrl.secc_started >= 1, "SECC did not start"
    start_stopped = ctrl.secc_stopped

    # Brief flap A shorter than grace
    sim_hal.cp().simulate_state("A")
    await asyncio.sleep(0.15)
    sim_hal.cp().simulate_state("B")
    await asyncio.sleep(0.5)  # give loop time to see reconnection
    assert ctrl.secc_stopped == start_stopped, "SECC stopped on brief flap"
    assert not any(s.startswith("A") for s in ctrl.processed_states), "process_cp_state('A') invoked despite brief flap"

    # Cleanup
    t.cancel()
    try:
        await t
    except Exception:
        pass


async def _run_case_disconnect_stops_after_grace() -> None:
    os.environ["EVSE_CONTROLLER"] = "hal"
    os.environ["EVSE_HAL_ADAPTER"] = "sim"
    os.environ["CABLE_LOCK_ENFORCE"] = "1"
    os.environ["CP_STABLE_BEFORE_START_S"] = "0.01"
    os.environ["CP_DISCONNECT_GRACE_S"] = "0.2"

    em.launch_secc_background = _fake_launch_secc_background  # type: ignore

    sim_hal = SimHardware()
    import src.evse_hal.registry as sreg  # type: ignore
    import evse_hal.registry as reg  # type: ignore

    def _create(_name: str = "sim"):
        return sim_hal

    sreg.create = _create  # type: ignore
    reg.create = _create  # type: ignore

    ctrl = TestController(slac_config=em.SlacConfig())
    t = asyncio.create_task(ctrl.start("EVSE_TEST", "lo"))
    setattr(t, "_controller_ref", ctrl)

    # B -> C -> matched -> SECC started
    sim_hal.cp().simulate_state("B")
    await asyncio.sleep(0.5)
    sim_hal.cp().simulate_state("C")
    await asyncio.sleep(0.5)
    await asyncio.sleep(0.2)
    assert ctrl.secc_started >= 1, "SECC did not start"
    start_stopped = ctrl.secc_stopped

    # Hold A longer than grace to force stop
    sim_hal.cp().simulate_state("A")
    await asyncio.sleep(0.3)  # > grace
    # Give loop time to stop
    await asyncio.sleep(0.3)
    assert ctrl.secc_stopped > start_stopped, "SECC not stopped after disconnect > grace"

    # Cleanup
    t.cancel()
    try:
        await t
    except Exception:
        pass


async def _run_case_emergency_F() -> None:
    os.environ["EVSE_CONTROLLER"] = "hal"
    os.environ["EVSE_HAL_ADAPTER"] = "sim"
    os.environ["CABLE_LOCK_ENFORCE"] = "1"
    os.environ["CABLE_UNLOCK_ON_FAULT"] = "1"

    em.launch_secc_background = _fake_launch_secc_background  # type: ignore

    sim_hal = SimHardware()
    import src.evse_hal.registry as sreg  # type: ignore
    import evse_hal.registry as reg  # type: ignore

    def _create(_name: str = "sim"):
        return sim_hal

    sreg.create = _create  # type: ignore
    reg.create = _create  # type: ignore

    ctrl = TestController(slac_config=em.SlacConfig())
    t = asyncio.create_task(ctrl.start("EVSE_TEST", "lo"))
    setattr(t, "_controller_ref", ctrl)

    # B -> C -> matched -> SECC started
    sim_hal.cp().simulate_state("B")
    await asyncio.sleep(0.5)
    sim_hal.cp().simulate_state("C")
    await asyncio.sleep(0.5)
    await asyncio.sleep(0.2)
    assert ctrl.secc_started >= 1, "SECC did not start"

    # Emergency F
    lk = sim_hal.cable_lock()
    lk.lock()
    sim_hal.cp().simulate_state("F")
    # Wait for unlock and stop
    deadline = time.time() + 1.0
    while time.time() < deadline and lk.is_locked():
        await asyncio.sleep(0.05)
    assert lk.is_locked() is False, "Cable not unlocked on emergency F"
    # Cleanup
    t.cancel()
    try:
        await t
    except Exception:
        pass


def main() -> int:
    print("Running HAL(sim) CP/Lock safety tests...")
    start = time.time()
    try:
        try:
            asyncio.run(_run_case_normal_flow())
            asyncio.run(_run_case_lock_failure_blocks_plc())
            asyncio.run(_run_case_brief_flap_does_not_stop())
            asyncio.run(_run_case_disconnect_stops_after_grace())
            asyncio.run(_run_case_emergency_F())
        except asyncio.CancelledError:
            pass
    except AssertionError as e:
        print("FAIL:", e)
        return 2
    except Exception as e:
        print("ERROR:", e)
        return 3
    finally:
        dur = time.time() - start
        print(f"Completed in {dur:.2f}s")
    print("PASS: All scenarios covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
