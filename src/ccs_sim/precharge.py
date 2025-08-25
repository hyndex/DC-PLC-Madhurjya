import time

class DCPowerSupplySim:
    """
    Simulates a DC power supply (EVSE) that can source a specified voltage and current.
    """
    def __init__(self, max_voltage: float = 500.0, max_current: float = 200.0):
        # Example limits: 500 V, 200 A (typical DC fast charger capability)
        self.max_voltage = max_voltage
        self.max_current = max_current
        self.target_voltage = 0.0   # EV-requested voltage setpoint
        self.current_limit = max_current  # EV-requested current limit (dynamic in charging loop)
        self.voltage = 0.0          # present output voltage
        self.current = 0.0          # present output current (amps)

    def set_voltage(self, volts: float):
        """Directly set the output voltage (for simulation or step changes)."""
        self.voltage = max(0.0, min(volts, self.max_voltage))

    def set_current_limit(self, amps: float):
        """Set the maximum current the supply should allow (e.g., from EV's request)."""
        self.current_limit = min(amps, self.max_current)

    def step_towards_voltage(self, target: float, step: float):
        """
        Increment or decrement the output voltage toward a target by a given step, without exceeding current limit.
        Updates `self.voltage` and `self.current` to simulate a load drawing current.
        """
        # Determine the direction of adjustment
        if abs(target - self.voltage) < 1e-2:
            return  # already at target (within small tolerance)
        if target > self.voltage:
            # Ramp up voltage
            new_voltage = self.voltage + step
            if new_voltage > target:
                new_voltage = target
        else:
            # Ramp down voltage (if ever needed)
            new_voltage = self.voltage - step
            if new_voltage < target:
                new_voltage = target
        # Simulate current draw: assume EV draws what it requested or what supply can give limited by difference
        # Simplified: if increasing voltage, assume ~2A draw during precharge, else 0
        simulated_current = 0.0
        if new_voltage > self.voltage:
            # During ramp-up, assume small current flows (e.g., <= 2A for precharge)
            simulated_current = min(self.current_limit, 2.0)
        # Update supply state
        self.voltage = round(new_voltage, 2)
        self.current = round(simulated_current, 2)

    def get_status(self):
        """Return current status (voltage, current) as a tuple."""
        return self.voltage, self.current

class PrechargeSimulator:
    """
    Controls the pre-charge process: ramping the EVSE output to match EV's voltage, limiting current.
    """
    def __init__(self, supply: DCPowerSupplySim):
        self.supply = supply
        self.precharge_complete = False

    def run_precharge(self, target_voltage: float, max_current: float = 2.0, timeout: float = 5.0):
        """
        Perform pre-charge by raising supply voltage to `target_voltage` with current <= max_current.
        Returns True if successful, False if timeout or error.
        """
        self.precharge_complete = False
        self.supply.set_current_limit(max_current)  # typically 2A
        start_time = time.time()
        print(f"[Precharge] Starting precharge to {target_voltage:.1f} V with <= {max_current} A")
        # Loop until voltage nearly reaches target or timeout
        while time.time() - start_time < timeout:
            # Step the supply voltage up by small increments (e.g., 1 V step)
            self.supply.step_towards_voltage(target_voltage, step=1.0)
            volts, amps = self.supply.get_status()
            # Log the status for debugging
            print(f"[Precharge] Voltage = {volts:.1f} V, Current = {amps:.2f} A")
            # Check if we've reached target (within a threshold)
            if volts >= target_voltage - 1.0:
                # Consider precharge done when we're within ~1V of target
                self.precharge_complete = True
                break
            time.sleep(0.1)  # 100 ms step delay to simulate ramp time
        if not self.precharge_complete:
            print("[Precharge] Timeout or incomplete precharge!")
        else:
            print("[Precharge] Precharge complete.")
        # Reset current limit to full (EVSE can provide more current after precharge)
        self.supply.set_current_limit(self.supply.max_current)
        return self.precharge_complete
