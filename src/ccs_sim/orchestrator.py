import time
import threading
from enum import Enum
from typing import Optional, Dict, Any
try:
    from src.evse_hal.interfaces import EVSEHardware
    from src.evse_hal import registry as hal_registry
except ImportError:  # executed as part of src.ccs_sim.* package (module path already includes src)
    from evse_hal.interfaces import EVSEHardware
    from evse_hal import registry as hal_registry
try:
    from . import pwm  # package import
    from .precharge import DCPowerSupplySim, PrechargeSimulator
    from .emeter import EnergyMeterSim
except ImportError:  # fallback when executed as a script
    import pwm
    from precharge import DCPowerSupplySim, PrechargeSimulator
    from emeter import EnergyMeterSim

class Phase(str, Enum):
    IDLE = "IDLE"
    HANDSHAKE = "HANDSHAKE"
    PRECARGE = "PRECHARGE"
    CHARGING = "CHARGING"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"


class ChargeOrchestrator:
    def __init__(self, hal: Optional[EVSEHardware] = None):
        # Initialize subsystems
        self.hal: EVSEHardware = hal or hal_registry.create("sim")
        self.precharger = PrechargeSimulator(self.hal.supply())
        self.session_active = False
        self.phase: Phase = Phase.IDLE
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_session_summary: Optional[Dict[str, Any]] = None

    def wait_for_vehicle(self):
        """
        Wait until a vehicle is detected (CP state B).
        For simulation, this could be triggered externally or by a manual call.
        On real hardware, poll the CP voltage until it drops to ~9V (State B).
        """
        print("[Orchestrator] Waiting for vehicle connection (State A to B)...")
        # Simulation: directly call simulate_cp_state for testing, in real use CP ADC
        while True:
            voltage = self.cp.read_cp_voltage()
            if voltage < 11.0:  # heuristic: below ~11V means a car is present
                # Enter State B
                self.session_active = True
                print("[Orchestrator] Vehicle detected! CP Voltage ~ {:.1f} V".format(voltage))
                break
            time.sleep(0.5)

    def run_session(self, target_voltage: float = 400.0, initial_current: float = 50.0, duration_s: float = 10.0):
        """Run a full charging session sequence once a vehicle is connected."""
        with self._lock:
            self.session_active = True
            self.phase = Phase.HANDSHAKE
            self.error = None
            self.last_session_summary = None
            self._stop_event.clear()
        # 1. Vehicle detected (state B). Start High-Level Communication (HLC) via PLC.
        # In real scenario, at this point SLAC matching and ISO 15118 session starts.
        print("[Orchestrator] Starting PLC handshake (SLAC) ...")
        # Simulate SLAC/ISO15118 handshake delay
        if self._wait_or_stop(2.0):
            return self._abort("STOPPED_DURING_HANDSHAKE")
        print("[Orchestrator] PLC link established. Starting ISO 15118 communication...")
        # Enter State C (vehicle ready) after handshake
        self.hal.cp().simulate_state("C")  # simulate EV moves to state C (6V)
        with self._lock:
            self.phase = Phase.PRECARGE
        # 2. Cable check
        print("[Orchestrator] Performing cable check...")
        # Ensure no voltage on DC lines and connector locked
        # (Simulation assumes connector is locked and no stray voltage)
        if self._wait_or_stop(1.0):
            return self._abort("STOPPED_DURING_CABLE_CHECK")
        print("[Orchestrator] Cable check passed. EVSE ready for pre-charge.")
        # 3. Pre-charge phase
        # Get target voltage from EV (for simulation, choose a target arbitrarily or preset)
        # EV would also request <=2A current for precharge (implicitly handled by PrechargeSimulator)
        pre_ok = self.precharger.run_precharge(target_voltage, max_current=2.0, timeout=10.0, stop_event=self._stop_event)
        if not pre_ok:
            print("[Orchestrator] Pre-charge failed or timed out, aborting session.")
            return self._abort("PRECHARGE_FAILED")
        # Precharge complete, now close contactors (simulate by just assuming they are closed)
        print("[Orchestrator] Closing contactor and starting energy transfer.")
        with self._lock:
            self.hal.contactor().set_closed(True)
            self.phase = Phase.CHARGING
        # 4. Charging loop – simulate a simple charging profile
        charging_duration = duration_s  # seconds to simulate charging
        start_time = time.time()
        requested_current = initial_current  # EV initial current request (A)
        self.hal.supply().set_current_limit(requested_current)
        while time.time() - start_time < charging_duration:
            if self._stop_event.is_set():
                return self._abort("STOP_REQUESTED")
            # Simulate EV updating current request (e.g., ramp down as battery fills)
            # For simplicity, reduce current request over time
            elapsed = time.time() - start_time
            if elapsed > 5:  # after 5 seconds, simulate tapering current
                requested_current = 30.0
                self.hal.supply().set_current_limit(requested_current)
            # EVSE supplies whatever is requested (within limit), so current = requested_current (simulate).
            # We'll simulate that voltage remains near target (battery voltage).
            self.hal.supply().set_voltage(target_voltage)  # maintain target voltage
            # Simulate measured current from requested if contactor is closed (sim only)
            if self.hal.contactor().is_closed():
                s = self.hal.supply()
                try:
                    impl = getattr(s, "_impl", None)
                    if impl is not None and hasattr(impl, "max_current"):
                        impl.current = min(requested_current, impl.max_current)
                except Exception:
                    pass
            # Contactor open means no output (simulate by zeroing status)
            volts, amps = self.hal.supply().get_status()
            if not self.hal.contactor().is_closed():
                volts, amps = 0.0, 0.0
            # Update energy meter with current measurements
            self.hal.meter().update(volts, amps)
            print(f"[Orchestrator] Supplying {amps:.1f} A at {volts:.1f} V")
            if self._wait_or_stop(1.0):
                return self._abort("STOP_REQUESTED")
        # 5. Charging complete – simulate EV sending stop request
        print("[Orchestrator] EV charging complete or stop requested.")
        # Open contactors (simulate instantly)
        self.hal.cp().simulate_state("B")  # vehicle still present but not charging
        self._complete_session()

    # Control and utilities
    def start_session(self, target_voltage: float = 400.0, initial_current: float = 50.0, duration_s: float = 10.0) -> bool:
        with self._lock:
            if self.session_active:
                return False
            self._thread = threading.Thread(target=self.run_session, args=(target_voltage, initial_current, duration_s), daemon=True)
            self._thread.start()
            return True

    def stop_session(self):
        self._stop_event.set()

    def set_contactor(self, closed: bool):
        with self._lock:
            self.hal.contactor().set_closed(bool(closed))

    def set_pwm_duty(self, duty: float):
        self.hal.pwm().set_duty(duty)

    def set_cp_state(self, state: str):
        self.hal.cp().simulate_state(state)

    def inject_fault(self, fault_type: str):
        with self._lock:
            self.error = fault_type
        self.stop_session()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            volts, amps = self.hal.supply().get_status()
            return {
                "session_active": self.session_active,
                "phase": self.phase,
                "error": self.error,
                "contactor_closed": self.hal.contactor().is_closed(),
                "cp_state": self.hal.cp().get_state(),
                "voltage": volts,
                "current": amps,
                "energy_Wh": self.hal.meter().get_energy_Wh(),
                "time_s": round(self.hal.meter().get_session_time_s(), 1),
                "last_session_summary": self.last_session_summary,
            }

    # Internal helpers
    def _wait_or_stop(self, seconds: float) -> bool:
        """Sleep in small intervals, return True if stop_event was set."""
        end = time.time() + seconds
        while time.time() < end:
            if self._stop_event.is_set():
                return True
            time.sleep(0.05)
        return False

    def _abort(self, reason: str):
        with self._lock:
            self.error = reason if self.error is None else self.error
            self.phase = Phase.ABORTED
            self.hal.contactor().set_closed(False)
            self.session_active = False
            # Record summary prior to reset
            self.last_session_summary = self._build_summary()
            self.hal.meter().reset()
        print(f"[Orchestrator] Session aborted: {self.error}")

    def _complete_session(self):
        # Log session summary
        energy = self.hal.meter().get_energy_Wh()
        avg_v = self.hal.meter().get_avg_voltage()
        avg_i = self.hal.meter().get_avg_current()
        duration = self.hal.meter().get_session_time_s()
        print("[Orchestrator] Session finished.")
        print(f" Total energy delivered: {energy:.3f} Wh")
        print(f" Average voltage: {avg_v:.1f} V, Average current: {avg_i:.1f} A")
        print(f" Session duration: {duration:.1f} seconds")
        with self._lock:
            self.phase = Phase.COMPLETE
            self.hal.contactor().set_closed(False)
            self.session_active = False
            self.last_session_summary = self._build_summary()
            self.hal.meter().reset()

    def _build_summary(self) -> Dict[str, Any]:
        return {
            "energy_Wh": self.hal.meter().get_energy_Wh(),
            "avg_voltage": self.hal.meter().get_avg_voltage(),
            "avg_current": self.hal.meter().get_avg_current(),
            "duration_s": round(self.hal.meter().get_session_time_s(), 1),
            "ended_phase": self.phase,
            "error": self.error,
        }

if __name__ == "__main__":
    orchestrator = ChargeOrchestrator()
    orchestrator.wait_for_vehicle()
    orchestrator.run_session()
