from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import serial  # type: ignore


logger = logging.getLogger("esp.periph")


@dataclass
class MeterSample:
    voltage_v: float
    current_a: float
    power_kw: float
    energy_kwh: float


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


class EspPeriphClient:
    """JSON-RPC over UART client for the ESP32-S3 peripheral coprocessor.

    Protocol: one JSON object per line (newline-delimited). Top-level schema:
    {"type":"req|res|evt","id":"...","ts":..., "method":..., "params":..., "result":..., "error":...}

    - Maintains a background reader thread to handle responses and events.
    - Provides a keepalive thread that sends sys.ping periodically.
    - Auto-arms before dangerous operations (contactor.set) unless disabled.
    - Reconnects on serial failures with backoff.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: int = 115200,
        timeout_s: float = 0.2,
        auto_keepalive: bool = True,
        keepalive_period_s: float = 1.5,
        auto_arm: bool = True,
    ) -> None:
        self._port = port or os.environ.get("ESP_PERIPH_PORT", "/dev/ttyUSB0")
        self._baud = baud
        self._timeout = timeout_s
        self._ser: Optional[serial.Serial] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_lock = threading.Lock()
        self._stop = threading.Event()
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._evt_cb: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._err_streak = 0
        self._mode: str = "sim"
        # CP status tracking (from firmware periodic 'status' frames)
        self._cp_last: Optional[CPStatus] = None
        self._cp_pong = threading.Event()
        # Keepalive
        self._auto_keepalive = auto_keepalive
        self._keepalive_period_s = max(0.2, keepalive_period_s)
        self._ka_thread: Optional[threading.Thread] = None
        # Auto-arm
        self._auto_arm = auto_arm
        self._armed_until_ms = 0

    # ----- Connection lifecycle -----
    def connect(self) -> None:
        self._open_serial()
        self._stop.clear()
        self._rx_thread = threading.Thread(target=self._rx_loop, name="esp-periph-rx", daemon=True)
        self._rx_thread.start()
        if self._auto_keepalive:
            self._ka_thread = threading.Thread(target=self._keepalive_loop, name="esp-periph-ka", daemon=True)
            self._ka_thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._rx_thread and self._rx_thread.is_alive():
            try:
                self._rx_thread.join(timeout=1.0)
            except Exception:
                pass
        if self._ka_thread and self._ka_thread.is_alive():
            try:
                self._ka_thread.join(timeout=1.0)
            except Exception:
                pass
        try:
            if self._ser and getattr(self._ser, "is_open", False):
                self._ser.close()
        except Exception:
            pass

    def on_event(self, cb: Callable[[str, Dict[str, Any]], None]) -> None:
        """Register an event callback: cb(event_name, payload)."""
        self._evt_cb = cb

    # ----- JSON-RPC core -----
    def _open_serial(self) -> None:
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=self._timeout)
            logger.info("ESP periph serial open", extra={"port": self._port, "baud": self._baud})
            self._err_streak = 0
        except Exception as e:
            logger.error("ESP periph serial open failed", extra={"port": self._port, "error": str(e)})
            raise

    def _send_line(self, line: str) -> None:
        if not self._ser or not getattr(self._ser, "is_open", False):
            self._open_serial()
        try:
            with self._tx_lock:
                assert self._ser is not None
                self._ser.write(line.encode("utf-8"))
        except Exception as e:
            logger.warning("ESP periph TX error", extra={"error": str(e)})
            # try one reopen
            try:
                self._open_serial()
                assert self._ser is not None
                with self._tx_lock:
                    self._ser.write(line.encode("utf-8"))
            except Exception as e2:
                logger.error("ESP periph TX failed after reopen", extra={"error": str(e2)})
                raise

    def send_req(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 1.0) -> Dict[str, Any]:
        rid = str(uuid.uuid4())
        obj = {"type": "req", "id": rid, "method": method, "params": params or {}}
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        done = threading.Event()
        slot: Dict[str, Any] = {"event": done, "res": None, "err": None}
        self._pending[rid] = slot
        self._send_line(line)
        deadline = time.time() + max(0.05, timeout)
        while time.time() < deadline:
            if done.wait(timeout=0.05):
                break
        # Clean up pending
        self._pending.pop(rid, None)
        if slot["err"] is not None:
            raise RuntimeError(f"{method} -> {slot['err']}")
        if slot["res"] is not None:
            return slot["res"]
        raise TimeoutError(f"timeout waiting {method}")

    def _rx_loop(self) -> None:
        assert self._ser is not None
        ser = self._ser
        buf_errs = 0
        while not self._stop.is_set():
            try:
                line = ser.readline()
            except Exception:
                self._err_streak += 1
                if self._err_streak >= 5:
                    try:
                        if self._ser:
                            try:
                                self._ser.close()
                            except Exception:
                                pass
                        self._open_serial()
                        assert self._ser is not None
                        ser = self._ser
                    except Exception:
                        time.sleep(0.2)
                        continue
                    self._err_streak = 0
                time.sleep(0.05)
                continue
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8").strip())
            except Exception as e:
                buf_errs += 1
                if buf_errs % 20 == 1:
                    logger.debug("ESP periph RX parse", extra={"error": str(e)})
                continue
            buf_errs = 0
            if not isinstance(msg, dict):
                continue
            mtype = str(msg.get("type", "")).lower()
            if mtype == "res":
                rid = str(msg.get("id", ""))
                slot = self._pending.get(rid)
                if slot is None:
                    continue
                if "error" in msg and msg["error"]:
                    slot["err"] = msg["error"]
                else:
                    slot["res"] = msg.get("result", {})
                slot["event"].set()
            elif mtype == "evt":
                name = str(msg.get("method", ""))
                payload = msg.get("result", {}) or {}
                if self._evt_cb:
                    try:
                        self._evt_cb(name, payload if isinstance(payload, dict) else {})
                    except Exception:
                        pass
                if name == "evt:contactor.change":
                    # invalidate cached arm on forced off
                    if not payload.get("on", False):
                        self._armed_until_ms = 0
            elif mtype == "status":
                # CP helper periodic status frame
                try:
                    mv = int(msg.get("cp_mv", 0))
                    mv_r = int(msg.get("cp_mv_robust", mv))
                    st = str(msg.get("state", "A"))[:1]
                    mode = str(msg.get("mode", "dc"))
                    pwm_obj = msg.get("pwm", {}) or {}
                    pwm = PWMStatus(
                        enabled=bool(pwm_obj.get("enabled", False)),
                        duty=int(pwm_obj.get("duty", 0)),
                        hz=int(pwm_obj.get("hz", 1000)),
                    )
                    self._cp_last = CPStatus(
                        cp_mv=mv, state=st, pwm=pwm, ts=time.time(), mode=mode, cp_mv_robust=mv_r
                    )
                except Exception:
                    pass
            elif mtype == "pong":
                # CP helper pong
                self._cp_pong.set()
            elif mtype == "req":
                # device should not send req; ignore
                pass

    # ----- Convenience wrappers -----
    def sys_info(self, timeout: float = 1.0) -> Dict[str, Any]:
        res = self.send_req("sys.info", timeout=timeout)
        self._mode = str(res.get("mode", self._mode))
        return res

    def sys_ping(self, timeout: float = 0.5) -> Dict[str, Any]:
        res = self.send_req("sys.ping", timeout=timeout)
        return res

    def sys_set_mode(self, mode: str, timeout: float = 1.0) -> str:
        res = self.send_req("sys.set_mode", {"mode": str(mode)}, timeout=timeout)
        self._mode = str(res.get("mode", self._mode))
        return self._mode

    def sys_arm(self, token: Optional[str] = None, timeout: float = 0.5) -> int:
        params = {"token": token} if token else {}
        res = self.send_req("sys.arm", params, timeout=timeout)
        self._armed_until_ms = int(res.get("armed_until_ms", 0))
        return self._armed_until_ms

    def contactor_check(self, timeout: float = 0.5) -> Dict[str, Any]:
        return self.send_req("contactor.check", timeout=timeout)

    def contactor_set(self, on: bool, timeout: float = 1.0) -> Dict[str, Any]:
        now_ms = int(time.monotonic() * 1000)
        # Auto-arm within window
        if self._auto_arm and (self._armed_until_ms - now_ms) < 200:
            try:
                self.sys_arm()
            except Exception:
                pass
        return self.send_req("contactor.set", {"on": bool(on)}, timeout=timeout)

    def temps_read(self, timeout: float = 0.5) -> Dict[str, Any]:
        return self.send_req("temps.read", timeout=timeout)

    def meter_read(self, timeout: float = 0.5) -> MeterSample:
        res = self.send_req("meter.read", timeout=timeout)
        v = float(res.get("v", 0.0))
        i = float(res.get("i", 0.0))
        p = float(res.get("p", 0.0))
        e = float(res.get("e", 0.0))
        return MeterSample(voltage_v=v, current_a=i, power_kw=p, energy_kwh=e)

    # ----- Keepalive -----
    def _keepalive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sys_ping(timeout=0.4)
            except Exception as e:
                logger.debug("ESP periph ping error", extra={"error": str(e)})
            time.sleep(self._keepalive_period_s)

    # ----- CP helper compatibility (same UART) -----
    def _send_cp(self, obj: Dict[str, Any]) -> None:
        # CP commands are plain objects with a 'cmd' field
        if "cmd" not in obj:
            raise ValueError("cp command must include 'cmd'")
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        self._send_line(line)

    def cp_get_status(self, wait_s: float = 0.5) -> Optional[CPStatus]:
        deadline = time.time() + wait_s
        last_ts = self._cp_last.ts if self._cp_last else 0.0
        # Request on-demand refresh
        try:
            self._send_cp({"cmd": "get_status"})
        except Exception:
            pass
        while time.time() < deadline:
            cur = self._cp_last
            if cur and cur.ts > last_ts:
                return cur
            time.sleep(0.02)
        return self._cp_last

    def cp_set_pwm(self, duty_percent: int, enable: Optional[bool] = None, wait: bool = True, timeout: float = 1.0) -> Optional[CPStatus]:
        duty = max(0, min(100, int(duty_percent)))
        payload: Dict[str, Any] = {"cmd": "set_pwm", "duty": duty}
        if enable is not None:
            payload["enable"] = bool(enable)
        self._send_cp(payload)
        if wait:
            def _pred(st: CPStatus) -> bool:
                if st.mode != "manual":
                    return True
                ok = (st.pwm.duty == duty)
                if enable is not None:
                    ok = ok and (st.pwm.enabled == bool(enable))
                return ok

            return self._wait_cp(_pred, timeout)
        return None

    def _wait_cp(self, predicate, timeout: float = 1.0) -> Optional[CPStatus]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            cur = self._cp_last
            if cur and predicate(cur):
                return cur
            time.sleep(0.02)
        return self._cp_last

    def cp_set_mode(self, mode: str, wait: bool = True, timeout: float = 1.2) -> Optional[CPStatus]:
        if mode not in ("dc", "manual"):
            raise ValueError("mode must be 'dc' or 'manual'")
        self._send_cp({"cmd": "set_mode", "mode": mode})
        if wait:
            return self._wait_cp(lambda st: st and st.mode == mode, timeout)
        return None

    def cp_ping(self, timeout: float = 0.5) -> bool:
        self._cp_pong.clear()
        try:
            self._send_cp({"cmd": "ping"})
        except Exception:
            return False
        return self._cp_pong.wait(timeout)

    def cp_restart_slac_hint(self, reset_ms: int = 400) -> None:
        try:
            self._send_cp({"cmd": "restart_slac_hint", "ms": int(reset_ms)})
        except Exception:
            pass

    def cp_reset(self) -> None:
        try:
            self._send_cp({"cmd": "reset"})
        except Exception:
            pass
