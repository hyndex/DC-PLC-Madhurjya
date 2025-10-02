#!/usr/bin/env python3
"""
EVerest Python module wrapper for ESP32 HAL adapter.

This script is invoked directly by the manager. It wires the ESP32 peripheral
client to EVerest interfaces using everest.framework bindings.
"""
import os
import sys
import time
import signal
import threading

# Ensure the local 'python' package is importable
_HERE = os.path.dirname(__file__)
_PY_DIR = os.path.join(_HERE, 'python')
if _PY_DIR not in sys.path:
    sys.path.insert(0, _PY_DIR)

from everest.framework import Module as EvModule, RuntimeSession, log  # type: ignore
from module import EspPeriphClient, CPStatus, MeterSample  # type: ignore


class Runner:
    def __init__(self) -> None:
        self.session = RuntimeSession()
        self.m = EvModule(self.session)
        log.update_process_name(self.m.info.id)

        self._stop = threading.Event()
        self._pub_thread: threading.Thread | None = None
        self._client: EspPeriphClient | None = None
        # Internal state for PSU + contactor fallbacks
        self._psu_mode: str = 'Off'  # Off|Export|Import|Fault
        self._power_allowed: bool = False
        self._tgt_v: float = 0.0
        self._tgt_i: float = 0.0
        self._sim_v: float = 0.0
        self._sim_i: float = 0.0
        self._last_meter_ok_ts: float = 0.0
        self._last_cp_state: str | None = None
        self._last_kick_ts: float = 0.0
        try:
            self._kick_enable = bool(int(os.getenv('EVSE_DEBUG_FORCE_CP_C', '0')))
        except Exception:
            self._kick_enable = False

        setup = self.m.say_hello()
        cfg = setup.configs.module
        self.tty = str(cfg.get('tty', '/dev/ttyUSB0'))
        try:
            self.baud = int(cfg.get('baud', 115200))
        except Exception:
            self.baud = 115200

        # Install command handlers
        self.m.implement_command('evse_board_support', 'enable', self._cmd_bsp_enable)
        self.m.implement_command('evse_board_support', 'pwm_on', self._cmd_bsp_pwm_on)
        self.m.implement_command('evse_board_support', 'pwm_off', self._cmd_bsp_pwm_off)
        self.m.implement_command('evse_board_support', 'pwm_F', self._cmd_bsp_pwm_f)
        # Stubs for AC-related commands to satisfy interface (DC use-case)
        self.m.implement_command('evse_board_support', 'ac_read_pp_ampacity', self._cmd_bsp_ac_read_pp_ampacity)
        self.m.implement_command('evse_board_support', 'ac_set_overcurrent_limit_A', self._cmd_bsp_ac_set_overcurrent_limit)
        self.m.implement_command('evse_board_support', 'ac_switch_three_phases_while_charging', self._cmd_bsp_ac_switch_three_phases)
        self.m.implement_command('evse_board_support', 'allow_power_on', self._cmd_bsp_allow_power_on)
        self.m.implement_command('evse_board_support', 'evse_replug', self._cmd_bsp_evse_replug)
        self.m.implement_command('power_supply_DC', 'setMode', self._cmd_psu_set_mode)
        self.m.implement_command('power_supply_DC', 'setExportVoltageCurrent', self._cmd_psu_set_v_i)
        self.m.implement_command('power_supply_DC', 'setImportVoltageCurrent', self._cmd_psu_set_import_v_i)

        # Ready callback kicks off publishers
        self.m.init_done(self._on_ready)

        # Graceful stop on SIGTERM
        signal.signal(signal.SIGTERM, lambda *_: self.stop())

    # ----- Lifecycle -----
    def _on_ready(self) -> None:
        # Connect serial
        self._client = EspPeriphClient(port=self.tty, baud=self.baud)
        self._client.on_event(self._on_evt)
        try:
            self._client.connect()
        except Exception as e:
            log.warning(f"ESP periph connect failed: {e}")

        # Publish initial capabilities
        self._publish_caps()

        # Start publisher thread
        self._pub_thread = threading.Thread(target=self._pub_loop, name='esp32-hal-pub', daemon=True)
        self._pub_thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._pub_thread and self._pub_thread.is_alive():
                self._pub_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if self._client:
                self._client.close()
        except Exception:
            pass

    # ----- Event/publishers -----
    def _on_evt(self, name: str, payload: dict) -> None:
        if name == 'evt:contactor.change':
            on = bool(payload.get('on', False))
            ev = {'event': 'PowerOn' if on else 'PowerOff'}
            self.m.publish_variable('evse_board_support', 'event', ev)

    def _publish_caps(self) -> None:
        # Board support
        max_i = float(os.getenv('EVSE_MAX_CURRENT_A', '200'))
        bsp_caps = {
            'max_current_A_import': max_i,
            'min_current_A_import': 0.0,
            'max_phase_count_import': 1,
            'min_phase_count_import': 1,
            'max_current_A_export': 0.0,
            'min_current_A_export': 0.0,
            'max_phase_count_export': 1,
            'min_phase_count_export': 1,
            'supports_changing_phases_during_charging': False,
            'connector_type': 'IEC62196Type2Socket',
        }
        self.m.publish_variable('evse_board_support', 'capabilities', bsp_caps)

        # PSU
        max_v = float(os.getenv('EVSE_MAX_VOLTAGE_V', '920'))
        psu_caps = {
            'bidirectional': False,
            'current_regulation_tolerance_A': 1.0,
            'peak_current_ripple_A': 1.0,
            'max_export_voltage_V': max_v,
            'min_export_voltage_V': 0.0,
            'max_export_current_A': max_i,
            'min_export_current_A': 0.0,
            'max_export_power_W': max_v * max_i,
        }
        self.m.publish_variable('power_supply_DC', 'capabilities', psu_caps)

    def _pub_loop(self) -> None:
        c = self._client
        if c is None:
            return
        while not self._stop.is_set():
            try:
                st = c.cp_get_status(wait_s=0.05)
                if st:
                    ev = {'event': st.state[:1].upper()}
                    self.m.publish_variable('evse_board_support', 'event', ev)
                    # Optional repeated PWM kick to help CP transition from B to C
                    try:
                        now = time.time()
                        if self._kick_enable:
                            cur = st.state[:1].upper()
                            self._last_cp_state = cur
                            # If we are stuck in B for > 3s after allow_power_on, kick again
                            if cur == 'B' and self._power_allowed and (now - self._last_kick_ts) > 3.0:
                                try:
                                    c._send_cp({"cmd": "set_mode", "mode": "manual"})
                                    c._send_cp({"cmd": "set_pwm", "duty": 5, "enable": True})
                                    time.sleep(0.6)
                                    c._send_cp({"cmd": "set_mode", "mode": "dc"})
                                except Exception:
                                    pass
                                self._last_kick_ts = now
                    except Exception:
                        pass
                # Try real meter first
                published = False
                try:
                    m = c.meter_read()
                    self._last_meter_ok_ts = time.time()
                    self.m.publish_variable('power_supply_DC', 'voltage_current',
                                            {'voltage_V': m.voltage_v, 'current_A': m.current_a})
                    published = True
                except Exception:
                    published = False
                # Fallback simulation when module/meter are not connected
                if not published:
                    # Simple ramp towards target when power is allowed and mode != Off
                    tick_s = 0.2
                    dv_per_s = 60.0  # V/s ramp
                    di_per_s = 20.0  # A/s ramp
                    if self._power_allowed and self._psu_mode in ('Export', 'Import'):
                        dv = dv_per_s * tick_s
                        di = di_per_s * tick_s
                        # Ramp voltage first (precharge behavior)
                        if self._sim_v < self._tgt_v:
                            self._sim_v = min(self._tgt_v, self._sim_v + dv)
                        else:
                            self._sim_v = max(self._tgt_v, self._sim_v - dv)
                        # Current tracks target but stays within a small safe band if not provided
                        if self._tgt_i > 0:
                            if self._sim_i < self._tgt_i:
                                self._sim_i = min(self._tgt_i, self._sim_i + di)
                            else:
                                self._sim_i = max(self._tgt_i, self._sim_i - di)
                        else:
                            # default small precharge current
                            self._sim_i = 3.0
                    else:
                        self._sim_v = 0.0
                        self._sim_i = 0.0
                    self.m.publish_variable('power_supply_DC', 'voltage_current',
                                            {'voltage_V': float(self._sim_v), 'current_A': float(self._sim_i)})
                # Periodically publish current PSU mode
                self.m.publish_variable('power_supply_DC', 'mode', self._psu_mode)
            except Exception as e:
                log.debug(f"publisher loop error: {e}")
            time.sleep(0.2)

    # ----- Command handlers -----
    def _cmd_bsp_enable(self, args) -> bool:
        c = self._client
        if not c:
            return False
        try:
            value = bool(args if isinstance(args, bool) else args.get('value', False))
        except Exception:
            value = False
        if not value:
            try:
                c.cp_set_mode('manual')
                c.cp_set_pwm(0, enable=False)
            except Exception:
                pass
            try:
                c.dc_enable(False)
            except Exception:
                pass
            return True
        try:
            c.cp_set_mode('dc')
            return True
        except Exception:
            return False

    def _cmd_bsp_pwm_on(self, args) -> bool:
        c = self._client
        if not c:
            return False
        try:
            # Interface expects field name 'value' (0..100). Accept legacy 'duty_cycle' too.
            duty = args.get('value', args.get('duty_cycle', 100))
            duty = int(duty)
        except Exception:
            duty = 100
        try:
            c.cp_set_mode('manual')
            c.cp_set_pwm(duty, enable=True)
            return True
        except Exception:
            return False

    def _cmd_bsp_pwm_off(self, _args) -> bool:
        c = self._client
        if not c:
            return False
        try:
            c.cp_set_pwm(0, enable=False)
            return True
        except Exception:
            return False

    def _cmd_bsp_pwm_f(self, _args) -> bool:
        # Force F state via PWM off
        return self._cmd_bsp_pwm_off(_args)

    def _cmd_bsp_ac_read_pp_ampacity(self, _args):
        # Not applicable in DC; return minimal ampacity
        return {'ampacity_A': 0.0}

    def _cmd_bsp_ac_set_overcurrent_limit(self, _args) -> bool:
        # Not applicable in DC; accept and ignore
        return True

    def _cmd_bsp_ac_switch_three_phases(self, _args) -> bool:
        # Not applicable in DC
        return False

    def _cmd_bsp_allow_power_on(self, args) -> bool:
        # Allow power on/off: publish PowerOn/PowerOff immediately as a fallback
        try:
            # Accept shapes: {"value": {"allow_power_on": bool, "reason": str}} or {"allow_power_on": bool}
            val = args.get('value', args)
            allow = bool(val.get('allow_power_on', val if isinstance(val, bool) else False))
        except Exception:
            allow = False
        self._power_allowed = allow
        ev = {'event': 'PowerOn' if allow else 'PowerOff'}
        self.m.publish_variable('evse_board_support', 'event', ev)
        # Try to reflect in firmware for better observability
        try:
            if self._client:
                self._client.send_req('dc.enable', {'on': bool(allow)}, timeout=0.5)
                # Optional: force CP to C briefly to help vehicles that need PWM kick
                if allow and os.getenv('EVSE_DEBUG_FORCE_CP_C', '0') == '1':
                    try:
                        self._client._send_cp({"cmd": "set_mode", "mode": "manual"})
                        self._client._send_cp({"cmd": "set_pwm", "duty": 5, "enable": True})
                        time.sleep(1.0)
                    except Exception:
                        pass
                    try:
                        self._client._send_cp({"cmd": "set_mode", "mode": "dc"})
                    except Exception:
                        pass
        except Exception:
            pass
        return True

    def _cmd_bsp_evse_replug(self, _args) -> bool:
        # Simulate virtual replug by toggling PWM off briefly
        try:
            self._cmd_bsp_pwm_off(None)
            time.sleep(0.1)
            self._cmd_bsp_pwm_on({'duty_cycle': 100})
            return True
        except Exception:
            return False

    def _cmd_psu_set_mode(self, args) -> bool:
        try:
            mode = str(args.get('mode', 'Off'))
        except Exception:
            mode = 'Off'
        # Normalize
        if mode not in ('Off', 'Export', 'Import', 'Fault'):
            mode = 'Off'
        self._psu_mode = mode
        # Publish immediately so EvseManager sees state
        self.m.publish_variable('power_supply_DC', 'mode', self._psu_mode)
        return True

    def _cmd_psu_set_v_i(self, args) -> bool:
        c = self._client
        if not c:
            return False
        try:
            # Interface expects 'voltage' and 'current'. Accept *_V and *_A as fallback.
            v = args.get('voltage', args.get('voltage_V', 0.0))
            i = args.get('current', args.get('current_A', 0.0))
            v = float(v)
            i = float(i)
            # Track targets for simulation fallback
            self._tgt_v = v
            self._tgt_i = i
            c.dc_set(volts=v, amps=i)
            return True
        except Exception:
            return False

    def _cmd_psu_set_import_v_i(self, _args) -> bool:
        # Not supported in this HAL; accept and ignore
        return True


if __name__ == '__main__':
    r = Runner()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        r.stop()
