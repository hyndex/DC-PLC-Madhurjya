from __future__ import annotations

from typing import Type

from src.evse_hal.interfaces import EVSEHardware


def _load_adapter(key: str) -> Type[EVSEHardware]:
    """Return the adapter class for the given key using lazy imports.

    Lazy loading prevents import-time failures when optional dependencies
    (e.g., pyserial for the ESP UART adapter) are not installed but the
    adapter is not used.
    """
    k = key.lower()
    if k == "sim":
        from src.evse_hal.adapters.sim import SimHardware  # local import

        return SimHardware
    if k == "esp-uart":
        from src.evse_hal.adapters.esp_uart import ESPSerialHardware  # local import

        return ESPSerialHardware
    raise ValueError(f"Unknown EVSE hardware adapter '{key}'")


def create(name: str = "sim") -> EVSEHardware:
    """Create an EVSE hardware adapter instance by name.

    Known names: 'sim', 'esp-uart'
    """
    cls = _load_adapter(name)
    return cls()
