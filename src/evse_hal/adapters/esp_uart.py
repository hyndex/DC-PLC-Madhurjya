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

    def read_voltage(self) -> float:
        st = self._c.get_status(wait_s=0.2)
        if st:
            # Trust firmware-provided CP state without Pi-side debouncing
            try:
                self._last_state = (st.state or "").strip().upper()[:1] or self._last_state
            except Exception:
                pass
            v = st.cp_mv / 1000.0
            logger.debug(
                "HAL CP read",
                extra={
                    "voltage_v": v,
                    "state": getattr(st, "state", None),
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
            try:
                self._last_state = (st.state or "").strip().upper()[:1] or self._last_state
            except Exception:
                pass
        return self._last_state

    # No additional internals: we rely on firmware for state classification and stability


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
        # Configure firmware CP mode once on startup based on environment
        # Defaults to 'dc' (5% duty in B/C/D). For AC Type 2 use cases set:
        #   ESP_CP_MODE=manual  EVSE_AC_MAX_CURRENT_A=16   (or desired Amps)
        cp_mode = os.environ.get("ESP_CP_MODE", os.environ.get("EVSE_CP_MODE", "dc")).strip().lower()
        try:
            if cp_mode in ("ac", "manual"):
                self._client.set_mode("manual")
                # Compute IEC 61851 AC PWM duty: Imax[A] ≈ duty[%] * 0.6 for 10–85%
                try:
                    a = float(os.environ.get("EVSE_AC_MAX_CURRENT_A", "16"))
                except Exception:
                    a = 16.0
                duty = int(max(10, min(85, round(a / 0.6))))
                try:
                    self._client.set_pwm(duty, enable=True)
                except Exception as e:
                    logger.warning("HAL ESP set_pwm failed", extra={"error": str(e)})
                logger.info("HAL ESP set_mode(manual)", extra={"duty_percent": duty, "ac_max_a": a})
            else:
                self._client.set_mode("dc")
                logger.info("HAL ESP set_mode(dc)")
        except Exception:
            logger.warning("HAL ESP initial CP mode setup failed", extra={"cp_mode": cp_mode})
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

    # Current CP mode as reported by firmware ('dc' or 'manual')
    def cp_mode(self) -> Optional[str]:
        try:
            st = self._client.get_status(wait_s=0.3)
            return getattr(st, "mode", None)
        except Exception:
            return None

    # Convenience: set AC advertised current (Amps). Computes IEC 61851 duty and
    # ensures firmware is in manual (AC) mode. Safe NOOP if firmware rejects.
    def set_ac_current(self, amps: float) -> None:
        try:
            a = float(amps)
            duty = int(max(10, min(85, round(a / 0.6))))
        except Exception:
            duty = 27
        try:
            self._client.set_mode("manual")
            self._client.set_pwm(duty, enable=True)
            logger.info("HAL ESP AC current set", extra={"amps": amps, "duty_percent": duty})
        except Exception as e:
            logger.warning("HAL ESP AC current set failed", extra={"error": str(e)})

    # AC HLC nudge: briefly set PWM to 5% to coax the EV to start SLAC/HLC,
    # then restore previous duty. Safe for AC where normal operation uses manual PWM.
    def ac_hlc_nudge(self, reset_ms: int = 350) -> None:
        try:
            st = self._client.get_status(wait_s=0.3)
            prev_duty = int(getattr(getattr(st, "pwm", None), "duty", 27)) if st else 27
        except Exception:
            prev_duty = 27
        try:
            self._client.set_mode("manual")
            self._client.set_pwm(5, enable=True)
            time.sleep(max(0, reset_ms) / 1000.0)
        finally:
            try:
                self._client.set_pwm(prev_duty, enable=True)
            except Exception:
                pass
        logger.info("HAL ESP AC HLC nudge", extra={"reset_ms": reset_ms, "restore_duty": prev_duty})

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        try:
            self._fallback.close()
        except Exception:
            pass
