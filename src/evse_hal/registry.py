from __future__ import annotations

from typing import Dict

from src.evse_hal.interfaces import EVSEHardware
from src.evse_hal.adapters.sim import SimHardware


_REGISTRY: Dict[str, type[EVSEHardware]] = {
    "sim": SimHardware,
}


def create(name: str = "sim") -> EVSEHardware:
    key = name.lower()
    if key not in _REGISTRY:
        raise ValueError(f"Unknown EVSE hardware adapter '{name}'")
    return _REGISTRY[key]()
