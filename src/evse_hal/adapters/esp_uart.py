from __future__ import annotations

import os
from dataclasses import dataclass
import time
import logging
from typing import Optional, Tuple

from ..interfaces import (
    CPReader,
    ContactorDriver,
    DCPowerSupply,
    EVSEHardware,
    Meter,
    PWMController,
)
from ..esp_cp_client import EspCpClient
from .sim import SimHardware
from ..lock import CableLockSim

logger = logging.getLogger("hal.esp")

class _EspPWM(PWMController):
    def __init__(self, client: EspCpClient) -> None:
        self._c = client

    def set_duty(self, duty_percent: float) -> None:
        # Only meaningful in firmware manual mode; avoid spamming errors in dc mode
        st = self._c.get_status(wait_s=0.1)
        mode = getattr(st, "mode", None)
        logger.info("HAL PWM set_duty", extra={"duty_percent": duty_percent, "mode": mode})
        if mode != "manual":
            # Respect firmware policy in dc mode (fixed 5% / 100%)
            return
        try:
            self._c.set_pwm(int(duty_percent), enable=True)
        except Exception as e:
            logger.warning("HAL PWM set_duty failed", extra={"error": str(e)})


class _EspCP(CPReader):
    def __init__(self, client: EspCpClient) -> None:
        self._c = client
        self._last_state: Optional[str] = None
        # Debounce control-pilot state transitions to mitigate noise/glitches.
        # Default to 50 ms for non-emergency transitions. Allow override via env.
        try:
            self._debounce_s: float = float(os.environ.get("CP_DEBOUNCE_S", "0.05"))
        except Exception:
            self._debounce_s = 0.05
        # Internal tracking for raw vs debounced state
        self._raw_state: Optional[str] = None
        self._raw_since: float = 0.0
        self._debounced_state: Optional[str] = None
        self._debounced_since: float = 0.0

    def read_voltage(self) -> float:
        st = self._c.get_status(wait_s=0.2)
        if st:
            # Update debouncer and last known state based on status
            self._update_states_from_status(st)
            v = st.cp_mv / 1000.0
            logger.debug(
                "HAL CP read",
                extra={
                    "voltage_v": v,
                    "raw_state": st.state,
                    "debounced_state": self._debounced_state,
                    "mode": getattr(st, "mode", None),
                },
            )
            return v
        return 0.0

    def simulate_state(self, state: str) -> None:
        # Hardware-backed CP ignores simulations
        self._last_state = state

    def get_state(self) -> Optional[str]:
        st = self._c.get_status(wait_s=0.05)
        if st:
            self._update_states_from_status(st)
        return self._debounced_state or self._last_state

    # --- Internals ---
    def _update_states_from_status(self, st) -> None:
        now = time.time()
        raw = (st.state or "").strip().upper()[:1] or None
        if raw != self._raw_state:
            self._raw_state = raw
            self._raw_since = now
        # Initialize on first run
        if self._debounced_state is None and raw is not None:
            self._debounced_state = raw
            self._debounced_since = now
            self._last_state = raw
            logger.info("CP state (init)", extra={"state": raw})
            return
        # Emergency states E/F: apply no debounce for fail-safe reaction
        if raw in ("E", "F") and raw != self._debounced_state:
            prev = self._debounced_state
            self._debounced_state = raw
            self._debounced_since = now
            self._last_state = raw
            logger.warning(
                "CP emergency state",
                extra={"from": prev, "to": raw, "cp_mv": st.cp_mv, "mode": getattr(st, "mode", None)},
            )
            return
        # For normal transitions A/B/C/D, require stability for debounce_s
        if raw is not None and raw != self._debounced_state:
            stable = max(0.0, now - self._raw_since)
            if stable >= max(0.0, self._debounce_s):
                prev = self._debounced_state
                self._debounced_state = raw
                self._debounced_since = now
                self._last_state = raw
                logger.info(
                    "CP state",
                    extra={
                        "from": prev,
                        "to": raw,
                        "stable_ms": int(stable * 1000),
                        "cp_mv": st.cp_mv,
                        "mode": getattr(st, "mode", None),
                    },
                )
        else:
            # Maintain last state
            self._last_state = self._debounced_state or raw


@dataclass
class ESPSerialHardware(EVSEHardware):
    _client: EspCpClient
    _pwm: _EspPWM
    _cp: _EspCP
    _fallback: SimHardware
    _lock: CableLockSim

    def __init__(self, port: Optional[str] = None) -> None:
        self._client = EspCpClient(port=port or os.environ.get("ESP_CP_PORT"))
        self._client.connect()
        # Duplex check
        try:
            ok = self._client.ping(timeout=0.5)
            logger.info("HAL ESP ping", extra={"ok": ok})
        except Exception:
            logger.warning("HAL ESP ping failed")
        # Ensure firmware is in DC auto mode
        try:
            self._client.set_mode("dc")
            logger.info("HAL ESP set_mode(dc)")
        except Exception:
            logger.warning("HAL ESP set_mode(dc) failed")
        self._pwm = _EspPWM(self._client)
        self._cp = _EspCP(self._client)
        # reuse sim for the rest to keep plumbing simple
        self._fallback = SimHardware()
        # Optional cable lock: use a simulated lock by default (real HW can override)
        self._lock = CableLockSim()

    def pwm(self) -> PWMController:
        return self._pwm

    def cp(self) -> CPReader:
        return self._cp

    def contactor(self) -> ContactorDriver:
        return self._fallback.contactor()

    def supply(self) -> DCPowerSupply:
        return self._fallback.supply()

    def meter(self) -> Meter:
        return self._fallback.meter()

    # Optional helper: attempt to nudge EV/stack to restart SLAC by toggling CP duty
    def restart_slac_hint(self, reset_ms: int = 400) -> None:
        """Try prompting a fresh SLAC by briefly leaving DC 5% indication.

        Sequence:
        - Switch to manual and drive 100% duty for a short period
        - Return to dc mode (firmware enforces 5% in B/C/D)
        """
        try:
            # Prefer firmware-level precise pulse if available
            self._client.restart_slac_hint(reset_ms)
            logger.info("HAL ESP SLAC restart hint (fw) sent", extra={"reset_ms": reset_ms})
            return
        except Exception:
            pass
        # Fallback: host-driven toggling
        try:
            self._client.set_mode("manual")
            self._client.set_pwm(100, enable=True)
            time.sleep(max(0, reset_ms) / 1000.0)
            self._client.set_mode("dc")
            logger.info("HAL ESP SLAC restart hint (host) sent", extra={"reset_ms": reset_ms})
        except Exception as e:
            logger.warning("HAL ESP SLAC restart hint failed", extra={"error": str(e)})

    # Expose minimal ESP controls for diagnostics
    def esp_ping(self, timeout: float = 0.5) -> bool:
        try:
            return self._client.ping(timeout)
        except Exception:
            return False

    def esp_set_mode(self, mode: str) -> None:
        self._client.set_mode(mode)

    def esp_set_pwm(self, duty: int, enable: bool = True) -> None:
        self._client.set_pwm(int(duty), enable=enable)

    # Optional cable lock API for HAL consumers
    def cable_lock(self) -> CableLockSim:
        return self._lock
