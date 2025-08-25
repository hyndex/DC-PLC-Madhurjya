import time
try:
    from . import pwm  # package import
    from .precharge import DCPowerSupplySim, PrechargeSimulator
    from .emeter import EnergyMeterSim
except ImportError:  # fallback when executed as a script
    import pwm
    from precharge import DCPowerSupplySim, PrechargeSimulator
    from emeter import EnergyMeterSim

class ChargeOrchestrator:
    def __init__(self):
        # Initialize subsystems
        self.cp = pwm  # using functions from pwm.py
        self.supply = DCPowerSupplySim()
        self.precharger = PrechargeSimulator(self.supply)
        self.meter = EnergyMeterSim()
        self.session_active = False

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

    def run_session(self):
        """Run a full charging session sequence once a vehicle is connected."""
        # 1. Vehicle detected (state B). Start High-Level Communication (HLC) via PLC.
        # In real scenario, at this point SLAC matching and ISO 15118 session starts.
        print("[Orchestrator] Starting PLC handshake (SLAC) ...")
        # Simulate SLAC/ISO15118 handshake delay
        time.sleep(2.0)  # e.g., 2 seconds to complete SLAC matching
        print("[Orchestrator] PLC link established. Starting ISO 15118 communication...")
        # Enter State C (vehicle ready) after handshake
        self.cp.simulate_cp_state("C")  # simulate that EV moves to state C (6V) after readiness
        # 2. Cable check
        print("[Orchestrator] Performing cable check...")
        # Ensure no voltage on DC lines and connector locked
        # (Simulation assumes connector is locked and no stray voltage)
        time.sleep(1.0)  # simulate some delay for cable check procedure
        print("[Orchestrator] Cable check passed. EVSE ready for pre-charge.")
        # 3. Pre-charge phase
        # Get target voltage from EV (for simulation, choose a target arbitrarily or preset)
        target_voltage = 400.0  # Example: EV battery at 400 V
        # EV would also request <=2A current for precharge (implicitly handled by PrechargeSimulator)
        pre_ok = self.precharger.run_precharge(target_voltage, max_current=2.0, timeout=10.0)
        if not pre_ok:
            print("[Orchestrator] Pre-charge failed or timed out, aborting session.")
            self.session_active = False
            return
        # Precharge complete, now close contactors (simulate by just assuming they are closed)
        print("[Orchestrator] Closing contactor and starting energy transfer.")
        # 4. Charging loop – simulate a simple charging profile
        charging_duration = 10  # seconds to simulate charging
        start_time = time.time()
        requested_current = 50.0  # EV initial current request (A)
        self.supply.set_current_limit(requested_current)
        while time.time() - start_time < charging_duration:
            # Simulate EV updating current request (e.g., ramp down as battery fills)
            # For simplicity, reduce current request over time
            elapsed = time.time() - start_time
            if elapsed > 5:  # after 5 seconds, simulate tapering current
                requested_current = 30.0
                self.supply.set_current_limit(requested_current)
            # EVSE supplies whatever is requested (within limit), so current = requested_current (simulate).
            # We'll simulate that voltage remains near target (battery voltage).
            self.supply.set_voltage(target_voltage)  # maintain target voltage
            self.supply.current = min(requested_current, self.supply.max_current)
            # Update energy meter with current measurements
            volts, amps = self.supply.get_status()
            self.meter.update(volts, amps)
            print(f"[Orchestrator] Supplying {amps:.1f} A at {volts:.1f} V")
            time.sleep(1.0)  # 1-second intervals for this simulation loop
        # 5. Charging complete – simulate EV sending stop request
        print("[Orchestrator] EV charging complete or stop requested.")
        # Open contactors (simulate instantly)
        self.cp.simulate_cp_state("B")  # vehicle still present but not charging
        # Log session summary
        energy = self.meter.get_total_energy_wh()
        avg_v = self.meter.get_average_voltage()
        avg_i = self.meter.get_average_current()
        duration = self.meter.get_session_time()
        print("[Orchestrator] Session finished.")
        print(f" Total energy delivered: {energy:.3f} Wh")
        print(f" Average voltage: {avg_v:.1f} V, Average current: {avg_i:.1f} A")
        print(f" Session duration: {duration:.1f} seconds")
        # Reset state for next session
        self.session_active = False
        self.meter.reset()

if __name__ == "__main__":
    orchestrator = ChargeOrchestrator()
    orchestrator.wait_for_vehicle()
    orchestrator.run_session()
