import time

class EnergyMeterSim:
    def __init__(self):
        self.reset()

    def reset(self):
        """Initialize or reset the meter readings."""
        self.total_watt_seconds = 0.0
        self.start_time = time.time()
        self.last_update = self.start_time
        self.total_samples = 0
        self.cumulative_voltage = 0.0
        self.cumulative_current = 0.0

    def record_measurement(self, voltage: float, current: float, dt: float):
        """
        Record a new measurement of voltage (V) and current (A) over a time interval dt (sec).
        Accumulate energy and for averaging.
        """
        # Accumulate energy in watt-seconds (which will be converted to Wh)
        self.total_watt_seconds += voltage * current * dt
        # Accumulate for averages
        self.cumulative_voltage += voltage * dt
        self.cumulative_current += current * dt
        self.total_samples += dt  # total time essentially

    def update(self, voltage: float, current: float):
        """
        Convenience method: compute dt from last update, record measurement, and update timestamp.
        """
        now = time.time()
        dt = now - self.last_update
        if dt < 0:
            dt = 0
        self.record_measurement(voltage, current, dt)
        self.last_update = now

    def get_total_energy_wh(self) -> float:
        """Return total energy delivered in Wh (watt-hours)."""
        return round(self.total_watt_seconds / 3600.0, 3)

    def get_average_voltage(self) -> float:
        """Return time-weighted average voltage over the session."""
        if self.total_samples == 0:
            return 0.0
        return round(self.cumulative_voltage / self.total_samples, 2)

    def get_average_current(self) -> float:
        """Return time-weighted average current over the session."""
        if self.total_samples == 0:
            return 0.0
        return round(self.cumulative_current / self.total_samples, 2)

    def get_session_time(self) -> float:
        """Return total session duration in seconds."""
        return time.time() - self.start_time
