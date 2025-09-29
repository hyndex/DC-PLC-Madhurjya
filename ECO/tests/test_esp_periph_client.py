import sys
import json
import types
import time
import threading
import importlib


class _FakeSerial:
    def __init__(self, *args, **kwargs):
        self.port = args[0] if args else kwargs.get("port")
        self.baudrate = kwargs.get("baudrate") or kwargs.get("baud")
        self.timeout = kwargs.get("timeout", 0.2)
        self._rx = []  # list of bytes lines
        self._lock = threading.Lock()
        self.is_open = True
        self._armed_until = 0
        self._contactor = False
        self._writes = []
        # CP helper state
        self._cp_mode = "dc"
        self._cp_pwm_enabled = False
        self._cp_pwm_duty = 100

    def write(self, data: bytes):
        line = data.decode("utf-8").strip()
        # Expect one JSON line per write
        try:
            obj = json.loads(line)
        except Exception:
            return
        self._writes.append(obj)
        if obj.get("type") == "req":
            rid = obj.get("id")
            method = obj.get("method")
            res = {"type": "res", "id": rid, "ts": int(time.time() * 1000), "result": {}}
            if method == "sys.ping":
                res["result"] = {"up_ms": 111, "mode": "sim", "temps": {"mcu": 42.0}}
            elif method == "sys.info":
                res["result"] = {"fw": "esp-periph/0.1.0", "proto": 1, "mode": "sim", "capabilities": ["contactor", "meter", "temps.gun_a", "temps.gun_b"]}
            elif method == "sys.arm":
                self._armed_until = int(time.monotonic() * 1000) + 1500
                res["result"] = {"armed_until_ms": self._armed_until}
            elif method == "contactor.set":
                on = bool((obj.get("params") or {}).get("on", False))
                # Enforce arming window in fake
                now = int(time.monotonic() * 1000)
                if now > self._armed_until:
                    res = {"type": "res", "id": rid, "ts": now, "error": {"code": 1001, "message": "not_armed"}}
                else:
                    self._contactor = on
                    res["result"] = {"ok": True, "aux_ok": on, "took_ms": 60}
            elif method == "contactor.check":
                res["result"] = {"commanded": self._contactor, "aux_ok": self._contactor, "coil_ma": (120.0 if self._contactor else 0.0), "reason": "ok"}
            elif method == "temps.read":
                res["result"] = {"temps": {"gun_a": {"c": 35.2}, "gun_b": {"c": 35.0}}}
            elif method == "meter.read":
                res["result"] = {"v": 400.0, "i": (50.0 if self._contactor else 0.0), "p": (20.0 if self._contactor else 0.0), "e": 0.123}
            elif method == "meter.stream_start":
                # ack + emit a tick event
                res["result"] = {}
                evt = {"type": "evt", "id": 0, "ts": int(time.time() * 1000), "method": "evt:meter.tick", "result": {"v": 399.0, "i": 49.1, "p": 19.6, "e": 0.124}}
                with self._lock:
                    self._rx.append((json.dumps(evt) + "\n").encode("utf-8"))
            else:
                res = {"type": "res", "id": rid, "ts": int(time.time() * 1000), "error": {"code": -32601, "message": "unknown_method"}}
            with self._lock:
                self._rx.append((json.dumps(res) + "\n").encode("utf-8"))
        elif "cmd" in obj:
            # CP commands path
            cmd = obj.get("cmd")
            if cmd == "ping":
                with self._lock:
                    self._rx.append((json.dumps({"type": "pong"}) + "\n").encode("utf-8"))
            elif cmd == "set_mode":
                m = obj.get("mode", "dc")
                if m in ("dc", "manual"):
                    self._cp_mode = m
                # Emit a status reflecting new mode
                st = {
                    "type": "status",
                    "cp_mv": 2300,
                    "cp_mv_robust": 2300,
                    "state": "B",
                    "mode": self._cp_mode,
                    "pwm": {"enabled": self._cp_pwm_enabled, "duty": self._cp_pwm_duty, "hz": 1000},
                }
                with self._lock:
                    self._rx.append((json.dumps(st) + "\n").encode("utf-8"))
            elif cmd == "set_pwm":
                d = int(obj.get("duty", 100))
                self._cp_pwm_duty = max(0, min(100, d))
                if "enable" in obj:
                    self._cp_pwm_enabled = bool(obj.get("enable"))
                # Echo new status only in manual mode
                st = {
                    "type": "status",
                    "cp_mv": 2300,
                    "cp_mv_robust": 2300,
                    "state": "B",
                    "mode": self._cp_mode,
                    "pwm": {"enabled": self._cp_pwm_enabled, "duty": self._cp_pwm_duty, "hz": 1000},
                }
                with self._lock:
                    self._rx.append((json.dumps(st) + "\n").encode("utf-8"))
            elif cmd == "get_status":
                st = {
                    "type": "status",
                    "cp_mv": 2300,
                    "cp_mv_robust": 2300,
                    "state": "B",
                    "mode": self._cp_mode,
                    "pwm": {"enabled": self._cp_pwm_enabled, "duty": self._cp_pwm_duty, "hz": 1000},
                }
                with self._lock:
                    self._rx.append((json.dumps(st) + "\n").encode("utf-8"))

    def readline(self) -> bytes:
        t0 = time.time()
        while time.time() - t0 < (self.timeout or 0.1):
            with self._lock:
                if self._rx:
                    return self._rx.pop(0)
            time.sleep(0.01)
        return b""

    def close(self):
        self.is_open = False


def _install_fake_serial_module():
    fake = types.ModuleType("serial")
    fake.Serial = _FakeSerial
    sys.modules["serial"] = fake


def test_basic_roundtrip_and_auto_arm():
    _install_fake_serial_module()
    # Late import after monkeypatch
    client_mod = importlib.import_module("src.evse_hal.esp_periph_client")
    EspPeriphClient = client_mod.EspPeriphClient
    c = EspPeriphClient(port="/dev/null", auto_keepalive=False)
    c.connect()
    info = c.sys_info()
    assert info.get("mode") == "sim"
    # auto-arm path: contactor_set should send sys.arm before set
    fake_ser = c._ser  # type: ignore[attr-defined]
    c.contactor_set(True)
    # Validate order of writes contains both sys.arm and contactor.set
    methods = [w.get("method") for w in getattr(fake_ser, "_writes", [])]
    assert "sys.arm" in methods and "contactor.set" in methods
    assert methods.index("sys.arm") < methods.index("contactor.set")
    chk = c.contactor_check()
    assert chk.get("commanded") is True and chk.get("aux_ok") is True
    temps = c.temps_read()
    assert "temps" in temps and "gun_a" in temps["temps"]
    m = c.meter_read()
    assert m.voltage_v > 0.0

    # Stream start should invoke event callback once
    hits = {"n": 0}
    c.on_event(lambda name, payload: hits.__setitem__("n", hits["n"] + 1))
    c.send_req("meter.stream_start", {"period_ms": 200}, timeout=0.5)
    time.sleep(0.05)
    assert hits["n"] >= 1


def test_cp_control_and_status():
    _install_fake_serial_module()
    client_mod = importlib.import_module("src.evse_hal.esp_periph_client")
    EspPeriphClient = client_mod.EspPeriphClient
    c = EspPeriphClient(port="/dev/null", auto_keepalive=False)
    c.connect()
    # Mode default dc, switch to manual
    st0 = c.cp_get_status(wait_s=0.2)
    assert st0 is not None and st0.mode in ("dc", "manual")
    c.cp_set_mode("manual")
    st1 = c.cp_get_status(wait_s=0.2)
    assert st1 is not None and st1.mode == "manual"
    # Set PWM and verify status reflects duty/enabled
    c.cp_set_pwm(25, enable=True)
    st2 = c.cp_get_status(wait_s=0.2)
    assert st2 is not None and st2.pwm.duty == 25 and st2.pwm.enabled is True
    # cp ping
    assert c.cp_ping(timeout=0.2) is True
