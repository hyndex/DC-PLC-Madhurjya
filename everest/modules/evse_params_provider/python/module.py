import os
import time
import threading

try:
    from everest.framework import Module
except Exception:
    class Module:
        def __init__(self, *args, **kwargs): pass
        def publish(self, *args, **kwargs): pass
        def call(self, *args, **kwargs): pass

class EvseParamsProvider(Module):
    def __init__(self, config):
        super().__init__()
        self.cfg = config
        self._stop = threading.Event()
        self._last_set: dict | None = None

    def start(self):
        # On start, set an initial external derating based on config/env if any
        # Periodically push external derating to EvseManager via dc_external_derate interface
        t = threading.Thread(target=self._loop, name="evse-derate", daemon=True)
        t.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                max_current = float(os.getenv('EVSE_MAX_CURRENT_A', self.cfg.get('max_current_a', 200)))
            except Exception:
                max_current = float(self.cfg.get('max_current_a', 200))
            # Optional power limit
            try:
                max_power_w = float(os.getenv('EVSE_MAX_POWER_W', '0'))
            except Exception:
                max_power_w = 0.0
            payload = {}
            if max_current > 0:
                payload['max_export_current_A'] = max_current
            if max_power_w > 0:
                payload['max_export_power_W'] = max_power_w
            # Only send if changed
            if payload and payload != self._last_set:
                try:
                    # everestpy typically exposes requirement calls as:
                    # self.call(requirement_name, command_name, args_dict)
                    # Fallback: try alternative signature if available.
                    try:
                        self.call('dc_external_derate', 'set_external_derating', {'derate': payload})
                    except Exception:
                        # Some versions expose call_cmd
                        if hasattr(self, 'call_cmd'):
                            self.call_cmd('dc_external_derate', 'set_external_derating', {'derate': payload})
                        else:
                            raise
                    self._last_set = dict(payload)
                except Exception:
                    pass
            # Update every second (fast enough for derating; tune via env if needed)
            time.sleep(1.0)
