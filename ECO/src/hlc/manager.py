from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional, Dict, Any

from src.evse_hal.registry import create as create_hal


@dataclass
class HLCStatus:
    state: str = "stopped"  # stopped|starting|running|error
    error: Optional[str] = None
    protocol_state: Optional[str] = None
    iface: Optional[str] = None
    session_id: Optional[str] = None


class HLCManager:
    def __init__(self) -> None:
        self._status = HLCStatus()
        self._controller: Optional[HalEVSEController] = None
        self._task: Optional[asyncio.Task] = None

    # Lifecycle
    async def start(self, iface: str, secc_config_path: Optional[str] = None, certificate_store: Optional[str] = None) -> None:
        if self._task and not self._task.done():
            return
        if certificate_store:
            os.environ["PKI_PATH"] = certificate_store
        self._status = HLCStatus(state="starting", iface=iface)

        # Lazy imports to avoid iso15118 package import at module load time
        try:
            from src.evse_hal.iso15118_hal_controller import HalEVSEController
            from iso15118.secc import SECCHandler
            from iso15118.secc.secc_settings import Config as SeccConfig
            from iso15118.shared.exi_codec import ExificientEXICodec
        except Exception as e:
            self._status.state = "error"
            self._status.error = f"import_error: {e}"
            return

        # Select HAL adapter based on environment (default 'sim').
        adapter = os.environ.get("EVSE_HAL_ADAPTER", "sim")
        hal = create_hal(adapter)
        self._controller = HalEVSEController(hal)

        config = SeccConfig()
        config.load_envs(secc_config_path)
        config.iface = iface

        async def _run():
            try:
                self._status.state = "running"
                await SECCHandler(
                    exi_codec=ExificientEXICodec(),
                    evse_controller=self._controller,
                    config=config,
                ).start(config.iface)
            except Exception as e:  # pragma: no cover - runtime safety
                self._status.state = "error"
                self._status.error = str(e)

        loop = asyncio.get_event_loop()
        self._task = loop.create_task(_run())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._status.state = "stopped"

    # Observability
    def set_protocol_state(self, state) -> None:
        try:
            self._status.protocol_state = str(state)
        except Exception:
            self._status.protocol_state = None

    def status(self) -> Dict[str, Any]:
        return {
            "state": self._status.state,
            "error": self._status.error,
            "protocol_state": self._status.protocol_state,
            "iface": self._status.iface,
            "session_id": self._status.session_id,
        }

    # BMS snapshot from SECC EV data context
    def bms_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self._controller:
            return None
        ev = self._controller.get_ev_data_context()
        return {
            "evcc_id": getattr(ev, "evcc_id", None),
            "present_soc": getattr(ev, "present_soc", None),
            "present_voltage": getattr(ev, "present_voltage", None),
            "target_voltage": getattr(ev, "target_voltage", None),
            "target_current": getattr(ev, "target_current", None),
            "total_battery_capacity": getattr(ev, "total_battery_capacity", None),
            "energy_requests": {
                "target_energy_request": getattr(ev, "target_energy_request", None),
                "max_energy_request": getattr(ev, "max_energy_request", None),
                "min_energy_request": getattr(ev, "min_energy_request", None),
            },
            "soc_limits": {
                "min_soc": getattr(ev, "min_soc", None),
                "max_soc": getattr(ev, "max_soc", None),
                "target_soc": getattr(ev, "target_soc", None),
            },
        }


# Global manager instance for API process
hlc = HLCManager()
