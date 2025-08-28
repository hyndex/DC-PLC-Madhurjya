from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class SlacStatus:
    state: str = "IDLE"  # IDLE|MATCHING|MATCHED|FAILED
    ev_mac: Optional[str] = None
    nid: Optional[str] = None
    run_id: Optional[str] = None
    attenuation_db: Optional[float] = None
    last_updated: float = time.time()


class SlacManager:
    def __init__(self) -> None:
        self._s = SlacStatus()

    def start_matching(self) -> None:
        self._s.state = "MATCHING"
        self._s.last_updated = time.time()

    def matched(self, ev_mac: str, nid: Optional[str] = None, run_id: Optional[str] = None, attenuation_db: Optional[float] = None) -> None:
        self._s.state = "MATCHED"
        self._s.ev_mac = ev_mac
        self._s.nid = nid
        self._s.run_id = run_id
        self._s.attenuation_db = attenuation_db
        self._s.last_updated = time.time()

    def fail(self, reason: Optional[str] = None) -> None:
        self._s.state = "FAILED"
        self._s.last_updated = time.time()

    def status(self) -> Dict[str, Any]:
        return {
            "state": self._s.state,
            "ev_mac": self._s.ev_mac,
            "nid": self._s.nid,
            "run_id": self._s.run_id,
            "attenuation_db": self._s.attenuation_db,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._s.last_updated)),
        }


slac = SlacManager()

