import os
import time
import threading
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

try:
    # Real runtime for Python modules in EVerest
    from everestpy import Module
except Exception:
    # Minimal stub to allow local testing without everestd
    class Module:  # type: ignore
        def __init__(self, *_, **__):
            self._vars: Dict[str, Any] = {}
            self._logger = logging.getLogger("esp32_hal_adapter.stub")

        def publish(self, iface: str, var: str, value: Any) -> None:
            self._vars[(iface, var)] = value
            self._logger.debug("publish", extra={"iface": iface, "var": var, "value": value})

        def register_command(self, iface: str, name: str, handler):
            setattr(self, f"cmd_{iface}_{name}", handler)

from .esp_periph_client import EspPeriphClient, CPStatus, MeterSample


@dataclass
class Config:
    tty: str
    baud: int


log = logging.getLogger("esp32_hal_adapter")


class Esp32HalAdapter(Module):
    """EVerest HAL adapter for ESP32-S3 coprocessor.

    - Provides: evse_board_support, power_supply_DC
    - Bridges commands to/from the UART JSON-RPC implemented on the ESP32-S3
    - Publishes capabilities, telemetry and voltage/current periodically
    """

    def __init__(self, config):
        super().__init__()
        self.cfg = Config(tty=config.get('tty'), baud=int(config.get('baud', 115200)))
        self._stop = threading.Event()
        self._c: Optional[EspPeriphClient] = None
        self._allow_power_on: bool = False
        self._ready = False
        self._pub_thread: Optional[threading.Thread] = None
        # Cached
        self._last_cp: Optional[CPStatus] = None
        self._last_meter: Optional[MeterSample] = None

    # ---- Lifecycle ----
    def start(self):  # called by runtime
        self._connect()
        self._publish_capabilities_initial()
        self._pub_thread = threading.Thread(target=self._publisher_loop, name="esp32-hal-pub", daemon=True)
        self._pub_thread.start()
        # Register command handlers (names follow interface definitions)
        try:
            self.register_command('evse_board_support', 'enable', self.cmd_evse_board_support_enable)
            self.register_command('evse_board_support', 'pwm_on', self.cmd_evse_board_support_pwm_on)
            self.register_command('evse_board_support', 'pwm_off', self.cmd_evse_board_support_pwm_off)
            self.register_command('evse_board_support', 'pwm_F', self.cmd_evse_board_support_pwm_F)
            self.register_command('evse_board_support', 'allow_power_on', self.cmd_evse_board_support_allow_power_on)
            self.register_command('power_supply_DC', 'setMode', self.cmd_power_supply_DC_setMode)
            self.register_command('power_supply_DC', 'setExportVoltageCurrent', self.cmd_power_supply_DC_setExportVoltageCurrent)
        except Exception:
            # In stub mode register_command may not exist; ignore
            pass
        self._ready = True

    def stop(self):
        self._ready = False
        self._stop.set()
        try:
            if self._pub_thread and self._pub_thread.is_alive():
                self._pub_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if self._c:
                self._c.close()
        except Exception:
            pass

    # ---- Serial / ESP client ----
    def _connect(self):
        self._c = EspPeriphClient(port=self.cfg.tty, baud=self.cfg.baud)

        def _evt(name: str, payload: Dict[str, Any]):
            # Contactor changes -> publish PowerOn/PowerOff events
            if name == 'evt:contactor.change':
                on = bool(payload.get('on', False))
                ev = {'event': 'PowerOn' if on else 'PowerOff'}
                self.publish('evse_board_support', 'event', ev)

        self._c.on_event(_evt)
        self._c.connect()
        try:
            info = self._c.sys_info(timeout=1.0)
            log.info("ESP periph", extra={'mode': info.get('mode'), 'caps': info.get('capabilities')})
        except Exception as e:
            log.warning("ESP periph sys.info failed", extra={'error': str(e)})
        # Ensure CP helper in DC mode
        try:
            self._c.cp_set_mode('dc')
        except Exception:
            pass

    # ---- Publishers ----
    def _publish_capabilities_initial(self):
        # Board support caps (static for now)
        bsp_caps = {
            'max_current_A_import': float(os.getenv('EVSE_MAX_CURRENT_A', 200)),
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
        self.publish('evse_board_support', 'capabilities', bsp_caps)

        # DC power supply caps
        max_v = float(os.getenv('EVSE_MAX_VOLTAGE_V', 920))
        max_i = float(os.getenv('EVSE_MAX_CURRENT_A', 200))
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
        self.publish('power_supply_DC', 'capabilities', psu_caps)

    def _publisher_loop(self):
        assert self._c is not None
        while not self._stop.is_set():
            try:
                # CP status
                st = self._c.cp_get_status(wait_s=0.05)
                if st:
                    self._last_cp = st
                    # Map CP state to event A..F if changed significantly (debounce inside ESP firmware)
                    ev = {'event': st.state[:1].upper()}
                    self.publish('evse_board_support', 'event', ev)
                    # Telemetry
                    tel = {
                        'evse_temperature_C': float(os.getenv('EVSE_TEMP_C', '30')),
                        'plug_temperature_C': float(os.getenv('EVSE_PLUG_TEMP_C', '30')),
                        'fan_rpm': 0,
                        'supply_voltage_12V': 12.0,
                        'supply_voltage_minus_12V': -12.0,
                        'relais_on': bool(st.pwm.enabled),
                    }
                    self.publish('evse_board_support', 'telemetry', tel)
                # Meter sample
                try:
                    m = self._c.meter_read()
                    self._last_meter = m
                    self.publish('power_supply_DC', 'voltage_current', {'voltage_V': m.voltage_v, 'current_A': m.current_a})
                except Exception:
                    pass
            except Exception as e:
                log.debug("publisher loop error", extra={'error': str(e)})
            time.sleep(0.2)

    # ---- evse_board_support commands ----
    def cmd_evse_board_support_enable(self, value: bool):
        assert self._c is not None
        log.info("bsp.enable", extra={'value': value})
        if not value:
            # Disable: ensure PWM off and DC off
            try:
                self._c.cp_set_mode('manual')
                self._c.cp_set_pwm(0, enable=False)
            except Exception:
                pass
            try:
                self._c.dc_enable(False)
            except Exception:
                pass
        else:
            try:
                self._c.cp_set_mode('dc')
            except Exception:
                pass

    def cmd_evse_board_support_pwm_on(self, value: float):
        assert self._c is not None
        duty = max(0, min(100, int(value)))
        log.info("bsp.pwm_on", extra={'duty': duty})
        try:
            self._c.cp_set_mode('manual')
            self._c.cp_set_pwm(duty, enable=True)
        except Exception as e:
            log.warning("pwm_on failed", extra={'error': str(e)})

    def cmd_evse_board_support_pwm_off(self):
        assert self._c is not None
        log.info("bsp.pwm_off")
        try:
            self._c.cp_set_mode('manual')
            self._c.cp_set_pwm(0, enable=False)
        except Exception as e:
            log.warning("pwm_off failed", extra={'error': str(e)})

    def cmd_evse_board_support_pwm_F(self):
        # No explicit negative voltage control; simulate by disabling
        log.info("bsp.pwm_F (simulated as pwm_off)")
        self.cmd_evse_board_support_pwm_off()

    def cmd_evse_board_support_allow_power_on(self, value: Dict[str, Any]):
        # value: { allow_power_on: bool, reason: str }
        self._allow_power_on = bool(value.get('allow_power_on', False))
        log.info("bsp.allow_power_on", extra={'allow': self._allow_power_on, 'reason': value.get('reason')})

    # ---- power_supply_DC commands ----
    def cmd_power_supply_DC_setMode(self, mode: str, phase: str = "Other"):
        assert self._c is not None
        log.info("psu.setMode", extra={'mode': mode, 'phase': phase})
        # Guardrails around phase for clarity/logging
        # EvseManager sequences phases; we avoid enabling DC unless allowed and only on Export
        if mode == 'Off':
            try:
                self._c.dc_enable(False)
            except Exception:
                pass
        elif mode == 'Export':
            # During CableCheck/PreCharge phases, EvseManager will drive setpoints;
            # we rely on allow_power_on gating to avoid early enable.
            if not self._allow_power_on:
                log.warning("psu.setMode Export gated: allow_power_on=false", extra={'phase': phase})
                return
            try:
                self._c.dc_enable(True)
            except Exception:
                pass
        elif mode == 'Fault':
            try:
                self._c.dc_enable(False)
            except Exception:
                pass

    def cmd_power_supply_DC_setExportVoltageCurrent(self, voltage: float, current: float):
        assert self._c is not None
        v = max(0.0, float(voltage))
        i = max(0.0, float(current))
        log.debug("psu.setExportVoltageCurrent", extra={'V': v, 'A': i})
        try:
            self._c.dc_set(volts=v, amps=i)
        except Exception as e:
            log.warning("dc_set failed", extra={'error': str(e)})


if __name__ == '__main__':  # simple standalone probe
    logging.basicConfig(level=logging.INFO)
    cfg = {'tty': os.environ.get('ESP32_TTY', '/dev/ttyUSB0'), 'baud': int(os.environ.get('ESP32_BAUD', '115200'))}
    mod = Esp32HalAdapter(cfg)
    mod.start()
    try:
        time.sleep(2)
        mod.cmd_evse_board_support_pwm_on(5)
        time.sleep(1)
        mod.cmd_power_supply_DC_setExportVoltageCurrent(350.0, 10.0)
        time.sleep(2)
    finally:
        mod.stop()
