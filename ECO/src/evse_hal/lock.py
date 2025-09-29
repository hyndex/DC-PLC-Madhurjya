from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class CableLockSim:
    """Simple simulated cable lock driver with monitoring.

    Provides lock/unlock and an is_locked() monitor to allow gating
    safety logic (e.g., only proceed to PLC when locked).
    """

    _locked: bool = False
    _last_cmd_ts: float = 0.0
    _lock: threading.Lock = threading.Lock()

    def lock(self) -> None:
        with self._lock:
            self._locked = True
            self._last_cmd_ts = time.time()

    def unlock(self) -> None:
        with self._lock:
            self._locked = False
            self._last_cmd_ts = time.time()

    def is_locked(self) -> bool:  # not part of base interface; optional helper
        with self._lock:
            return self._locked

    def last_command_ts(self) -> float:
        with self._lock:
            return self._last_cmd_ts

