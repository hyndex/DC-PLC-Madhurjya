from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import serial  # type: ignore


@dataclass
class PWMStatus:
    enabled: bool
    duty: int
    hz: int


@dataclass
class CPStatus:
    cp_mv: int
    state: str
    pwm: PWMStatus
    ts: float
    mode: str = "dc"
    cp_mv_robust: int = 0


logger = logging.getLogger("esp.cp")


class EspCpClient:
    """Minimal client for the ESP32-S3 CP helper firmware (JSON over UART).

    - Periodic status frames are read in a background thread and kept as latest status
    - Commands are newline-delimited JSON objects
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: int = 115200,
        timeout_s: float = 0.2,
    ) -> None:
        # Prefer Raspberry Pi's stable alias when not specified
        self._port = port or os.environ.get("ESP_CP_PORT", "/dev/serial0")
        self._baud = baud
        self._timeout = timeout_s
        self._ser: Optional[serial.Serial] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last: Optional[CPStatus] = None
        self._pong = threading.Event()
        self._err_streak = 0

    def connect(self) -> None:
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=self._timeout)
        except Exception as e:
            logger.error("ESP CP serial open failed", extra={"port": self._port, "error": str(e)})
            raise
        self._stop.clear()
        logger.info("ESP CP serial connect", extra={"port": self._port, "baud": self._baud})
        self._rx_thread = threading.Thread(target=self._rx_loop, name="esp-cp-rx", daemon=True)
        self._rx_thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        if self._ser and self._ser.is_open:
            logger.info("ESP CP serial close")
            self._ser.close()

    # ----- Public API -----
    def get_status(self, wait_s: float = 0.5) -> Optional[CPStatus]:
        """Return latest status, optionally waiting for up to wait_s seconds for a fresh one."""
        deadline = time.time() + wait_s
        last_ts = self._last.ts if self._last else 0.0
        # Ask for on-demand refresh
        self._send({"cmd": "get_status"})
        while time.time() < deadline:
            with self._lock:
                cur = self._last
            if cur and cur.ts > last_ts:
                return cur
            time.sleep(0.02)
        with self._lock:
            return self._last

    def _wait_status(self, predicate, timeout: float = 1.0) -> Optional[CPStatus]:
        """Wait until predicate(latest_status) is True or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                cur = self._last
            if cur and predicate(cur):
                return cur
            time.sleep(0.02)
        with self._lock:
            return self._last

    def set_pwm(self, duty_percent: int, enable: Optional[bool] = None, wait: bool = True, timeout: float = 1.0) -> Optional[CPStatus]:
        duty = max(0, min(100, int(duty_percent)))
        payload: Dict[str, Any] = {"cmd": "set_pwm", "duty": duty}
        if enable is not None:
            payload["enable"] = bool(enable)
        self._send(payload)
        if wait:
            def _pred(st: CPStatus) -> bool:
                # In manual mode, status should reflect requested duty/enable
                if st.mode != "manual":
                    return True  # nothing to wait for in dc mode
                ok = (st.pwm.duty == duty)
                if enable is not None:
                    ok = ok and (st.pwm.enabled == bool(enable))
                return ok
            return self._wait_status(_pred, timeout)
        return None

    def enable_pwm(self, enable: bool) -> None:
        self._send({"cmd": "enable_pwm", "enable": bool(enable)})

    def set_freq(self, hz: int) -> None:
        self._send({"cmd": "set_freq", "hz": int(hz)})

    def set_mode(self, mode: str, wait: bool = True, timeout: float = 1.2) -> Optional[CPStatus]:
        if mode not in ("dc", "manual"):
            raise ValueError("mode must be 'dc' or 'manual'")
        self._send({"cmd": "set_mode", "mode": mode})
        if wait:
            return self._wait_status(lambda st: st.mode == mode, timeout)
        return None

    def ping(self, timeout: float = 0.5) -> bool:
        """Check duplex connectivity with a ping/pong."""
        self._pong.clear()
        self._send({"cmd": "ping"})
        return self._pong.wait(timeout)

    # ----- Internals -----
    def _send(self, obj: Dict[str, Any]) -> None:
        if not self._ser or not getattr(self._ser, "is_open", False):
            # Attempt a quick reconnect
            try:
                self.connect()
            except Exception:
                raise RuntimeError("Serial not connected")
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        try:
            self._ser.write(line.encode("utf-8"))
        except Exception:
            # One retry after reconnect
            try:
                self.connect()
                self._ser.write(line.encode("utf-8"))
            except Exception as e:
                logger.warning("UART TX failed", extra={"error": str(e)})
                raise
        logger.debug("UART TX", extra={"line": line.strip()})

    def _rx_loop(self) -> None:
        assert self._ser is not None
        ser = self._ser
        while not self._stop.is_set():
            try:
                line = ser.readline()
            except Exception:
                self._err_streak += 1
                if self._err_streak >= 10:
                    try:
                        if self._ser:
                            try:
                                self._ser.close()
                            except Exception:
                                pass
                        self._ser = serial.Serial(self._port, self._baud, timeout=self._timeout)
                        ser = self._ser
                        logger.info("ESP CP serial reconnected")
                        self._err_streak = 0
                    except Exception:
                        pass
                time.sleep(0.1)
                continue
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8").strip())
            except Exception:
                logger.debug("UART RX (non-JSON)", extra={"line": line.decode(errors="ignore").strip()})
                continue

            # Handle cases where firmware sends JSON that isn't an object
            # e.g., a bare string like "pong" or other primitives
            if isinstance(msg, str):
                if msg.strip().lower() == "pong":
                    self._pong.set()
                else:
                    logger.debug("UART RX (JSON string)", extra={"value": msg})
                continue
            if not isinstance(msg, dict):
                logger.debug(
                    "UART RX (JSON non-object)",
                    extra={"py_type": type(msg).__name__, "value": str(msg)[:120]},
                )
                continue

            logger.debug("UART RX", extra={"json": msg})
            mtype = msg.get("type")
            if mtype == "status":
                try:
                    mv = int(msg.get("cp_mv", 0))
                except Exception:
                    try:
                        mv = int(float(msg.get("cp_mv", 0)))
                    except Exception:
                        mv = 0
                try:
                    mv_r = int(msg.get("cp_mv_robust", mv))
                except Exception:
                    try:
                        mv_r = int(float(msg.get("cp_mv_robust", mv)))
                    except Exception:
                        mv_r = mv
                st = str(msg.get("state", "A"))[:1]
                mode = str(msg.get("mode", "dc"))
                pwm_obj = msg.get("pwm", {}) or {}
                pwm = PWMStatus(
                    enabled=bool(pwm_obj.get("enabled", False)),
                    duty=int(pwm_obj.get("duty", 0)),
                    hz=int(pwm_obj.get("hz", 1000)),
                )
                with self._lock:
                    self._last = CPStatus(cp_mv=mv, state=st, pwm=pwm, ts=time.time(), mode=mode, cp_mv_robust=mv_r)
            elif mtype == "pong":
                self._pong.set()
            elif mtype == "ok":
                # Acknowledge simple ok responses (e.g., restart_slac_hint / reset)
                pass

    # --- Convenience / extended commands ---
    def restart_slac_hint(self, reset_ms: int = 400) -> None:
        """Ask firmware to emit a short X1 pulse then return to dc mode."""
        self._send({"cmd": "restart_slac_hint", "ms": int(reset_ms)})

    def reset(self) -> None:
        """Request firmware reboot (link will drop)."""
        try:
            self._send({"cmd": "reset"})
        except Exception:
            pass
