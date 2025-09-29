from __future__ import annotations

from typing import Type

from .interfaces import EVSEHardware


def _load_adapter(key: str) -> Type[EVSEHardware]:
    """Return the adapter class for the given key using lazy imports.

    Lazy loading prevents import-time failures when optional dependencies
    (e.g., pyserial for the ESP UART adapter) are not installed but the
    adapter is not used.
    """
    k = key.lower()
    if k == "sim":
        from .adapters.sim import SimHardware  # local import

        return SimHardware
    if k == "esp-uart":
        from .adapters.esp_uart import ESPSerialHardware  # local import

        return ESPSerialHardware
    if k in ("esp-periph", "esp-periph-uart"):
        from .adapters.esp_periph_uart import ESPPeriphHardware  # local import

        return ESPPeriphHardware
    raise ValueError(f"Unknown EVSE hardware adapter '{key}'")


def create(name: str = "sim") -> EVSEHardware:
    """Create an EVSE hardware adapter instance by name.

    Known names: 'sim', 'esp-uart', 'esp-periph'
    """
    cls = _load_adapter(name)
    return cls()
