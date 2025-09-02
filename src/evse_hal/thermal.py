from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class ThermalReading:
    name: str
    temp_c: float
    ts: float


@dataclass
class ThermalDecision:
    state: str  # OK, DERATE, FAULT
    allowed_current_a: float
    hottest_sensor: Optional[str]
    hottest_temp_c: Optional[float]
    reason: str = ""


class ThermalManager:
    """Simple, robust thermal derating and cutoff manager.

    - Supports up to four conceptual sensors: connector, cable, rectifier, ambient
    - Uses env vars for thresholds by default; can be overridden at runtime
    - Provides linear derating between warn and shutdown thresholds
    - Latches a fault when shutdown is crossed; requires cooldown + hysteresis
    - Adds a voltage sag heuristic to detect hot connector under high current
    """

    def __init__(self) -> None:
        self._last_decision: Optional[ThermalDecision] = None
        self._fault_latched: bool = False
        self._fault_since: float = 0.0
        self._last_temps: Dict[str, ThermalReading] = {}
        # Hysteresis / cooldown tracking
        self._cooldown_entered_at: float = 0.0

        # Load default thresholds from environment
        self.cfg = self._load_config_from_env()

    # --- Configuration helpers ---
    @dataclass
    class Config:
        # temperature thresholds in C
        warn_c: Dict[str, float]
        shutdown_c: Dict[str, float]
        cooldown_c: float
        # linear derate start/end; if absent per-sensor, warn->shutdown is used
        derate_start_c: Dict[str, float]
        derate_end_c: Dict[str, float]
        # inference via voltage sag
        enable_sag_inference: bool
        sag_frac_threshold: float  # e.g. 0.07 means 7% below target at current
        sag_min_current_a: float
        sag_derate_fraction: float  # additional derate multiplier when sagging
        # derivative-based fast reaction
        fast_rise_c_per_s: float  # threshold to accelerate derating
        fast_rise_extra_derate: float  # extra derate multiplier when exceeded
        # cooldown
        fault_cooldown_hold_s: float

    def _load_config_from_env(self) -> "ThermalManager.Config":
        def f(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, default))
            except Exception:
                return default

        def dflt_map(name_prefix: str, defaults: Dict[str, float]) -> Dict[str, float]:
            out: Dict[str, float] = {}
            for k, v in defaults.items():
                out[k] = f(f"{name_prefix}_{k.upper()}_C", v)
            return out

        warn = dflt_map(
            "EVSE_THERMAL_WARN",
            {"CONNECTOR": 70.0, "CABLE": 75.0, "RECTIFIER": 85.0, "AMBIENT": 45.0},
        )
        shutdown = dflt_map(
            "EVSE_THERMAL_SHUTDOWN",
            {"CONNECTOR": 90.0, "CABLE": 95.0, "RECTIFIER": 100.0, "AMBIENT": 60.0},
        )
        # Use warn/shutdown if derate bounds not specified
        derate_start = dflt_map(
            "EVSE_THERMAL_DERATE_START",
            {k: warn[k] for k in warn},
        )
        derate_end = dflt_map(
            "EVSE_THERMAL_DERATE_END",
            {k: shutdown[k] for k in shutdown},
        )
        enable_sag = bool(int(os.environ.get("EVSE_THERMAL_ENABLE_SAG", "1")))
        return ThermalManager.Config(
            warn_c=warn,
            shutdown_c=shutdown,
            cooldown_c=f("EVSE_THERMAL_COOLDOWN_C", 50.0),
            derate_start_c=derate_start,
            derate_end_c=derate_end,
            enable_sag_inference=enable_sag,
            sag_frac_threshold=f("EVSE_THERMAL_SAG_FRAC", 0.07),
            sag_min_current_a=f("EVSE_THERMAL_SAG_MIN_A", 50.0),
            sag_derate_fraction=f("EVSE_THERMAL_SAG_DERATE", 0.5),
            fast_rise_c_per_s=f("EVSE_THERMAL_FAST_RISE_C_PER_S", 1.2),
            fast_rise_extra_derate=f("EVSE_THERMAL_FAST_RISE_EXTRA", 0.3),
            fault_cooldown_hold_s=f("EVSE_THERMAL_FAULT_HOLD_S", 30.0),
        )

    # --- Public API ---
    def read_sensors_from_env(self) -> Dict[str, ThermalReading]:
        """Reads optional sensor telemetry from environment variables.

        EVSE_THERMAL_SENSOR_<NAME>_C can be used to feed live values externally.
        Recognized names: CONNECTOR, CABLE, RECTIFIER, AMBIENT.
        """
        out: Dict[str, ThermalReading] = {}
        now = time.time()
        for name in ("CONNECTOR", "CABLE", "RECTIFIER", "AMBIENT"):
            env_name = f"EVSE_THERMAL_SENSOR_{name}_C"
            val = os.environ.get(env_name)
            if val is None:
                continue
            try:
                t = float(val)
            except Exception:
                continue
            out[name] = ThermalReading(name=name, temp_c=t, ts=now)
        return out

    def update(
        self,
        *,
        rated_current_a: float,
        target_voltage_v: float,
        target_current_a: float,
        measured_voltage_v: float,
        measured_current_a: float,
        extra_sensors: Optional[Dict[str, ThermalReading]] = None,
    ) -> ThermalDecision:
        """Compute allowed current given temperatures and electrical behavior.

        - rated_current_a: maximum EVSE capability
        - target_*: controller setpoints requested by EV
        - measured_*: actual output
        - extra_sensors: any external thermal sensor readings
        """
        now = time.time()
        # Merge sensors: external + env
        sensors = {**self.read_sensors_from_env(), **(extra_sensors or {})}
        # Update last temps
        for k, r in sensors.items():
            self._last_temps[k] = r

        # Evaluate hottest sensor and derive derate factor
        hottest_name: Optional[str] = None
        hottest_temp: Optional[float] = None
        derate_factor = 1.0
        fault = False
        reason_bits = []

        for name, reading in self._last_temps.items():
            t = reading.temp_c
            if hottest_temp is None or t > hottest_temp:
                hottest_temp = t
                hottest_name = name
            warn = self.cfg.warn_c.get(name, math.inf)
            shut = self.cfg.shutdown_c.get(name, math.inf)
            dstart = self.cfg.derate_start_c.get(name, warn)
            dend = self.cfg.derate_end_c.get(name, shut)
            if t >= shut:
                fault = True
                reason_bits.append(f"{name} {t:.1f}C>={shut}C")
            elif t >= dstart:
                # Linear derate between dstart and dend towards 0 at dend
                span = max(1e-3, dend - dstart)
                ratio = max(0.0, min(1.0, (dend - t) / span))
                derate_factor = min(derate_factor, ratio)
                reason_bits.append(f"{name} derate {ratio:.2f}")

        # Fast rise heuristic
        if hottest_name and hottest_name in self._last_temps:
            r = self._last_temps[hottest_name]
            # If we have a prior reading for the same sensor, estimate derivative
            # Note: we don't store historical series beyond last, by design simplicity
            # So derivative-based reduction is approximated using configured threshold
            # and the presence of a near-shutdown temperature.
            # This can be extended to EMA if needed.
            pass  # placeholder for potential future series

        # Voltage sag heuristic: large sag at high current suggests hot contacts
        if (
            self.cfg.enable_sag_inference
            and measured_current_a >= self.cfg.sag_min_current_a
            and target_voltage_v > 1.0
        ):
            sag = (target_voltage_v - measured_voltage_v) / target_voltage_v
            if sag >= self.cfg.sag_frac_threshold:
                # Apply multiplicative derate
                derate_factor = min(derate_factor, max(0.0, 1.0 - self.cfg.sag_derate_fraction))
                reason_bits.append(f"sag {sag*100:.1f}%")

        # Fault latching and cooldown logic
        if fault:
            if not self._fault_latched:
                self._fault_latched = True
                self._fault_since = now
            self._cooldown_entered_at = 0.0
        else:
            if self._fault_latched:
                # Check cooldown condition: all sensors below cooldown_c
                all_cool = True
                for name, reading in self._last_temps.items():
                    if reading.temp_c > self.cfg.cooldown_c:
                        all_cool = False
                        break
                if all_cool:
                    if self._cooldown_entered_at == 0.0:
                        self._cooldown_entered_at = now
                    if now - self._cooldown_entered_at >= self.cfg.fault_cooldown_hold_s:
                        # Clear fault latch
                        self._fault_latched = False
                        self._fault_since = 0.0
                        self._cooldown_entered_at = 0.0

        # Compute allowed current
        if self._fault_latched:
            allowed = 0.0
            state = "FAULT"
            if reason_bits:
                reason = "; ".join(reason_bits)
            else:
                reason = "latched"
        else:
            # Additional fast-rise penalty if near shutdown and heating quickly
            extra_penalty = 1.0
            # Without historical slope, approximate via closeness to shutdown
            if hottest_name and hottest_temp is not None:
                shut = self.cfg.shutdown_c.get(hottest_name, math.inf)
                warn = self.cfg.warn_c.get(hottest_name, -math.inf)
                if hottest_temp > warn:
                    proximity = max(0.0, (hottest_temp - warn) / max(1e-3, shut - warn))
                    if proximity > 0.8:
                        extra_penalty -= min(1.0, self.cfg.fast_rise_extra_derate)
            eff_factor = max(0.0, min(1.0, derate_factor * extra_penalty))
            # Don't increase beyond either EV request or rated
            allowed = max(0.0, min(rated_current_a, target_current_a)) * eff_factor
            state = "DERATE" if eff_factor < 0.999 else "OK"
            reason = ", ".join(reason_bits) if reason_bits else ""

        dec = ThermalDecision(
            state=state,
            allowed_current_a=allowed,
            hottest_sensor=hottest_name,
            hottest_temp_c=hottest_temp,
            reason=reason,
        )
        self._last_decision = dec
        return dec

    # Utility accessors
    def last_decision(self) -> Optional[ThermalDecision]:
        return self._last_decision

