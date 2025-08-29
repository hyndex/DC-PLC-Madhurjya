#!/usr/bin/env python3
"""Convenience script to start SLAC and ISO 15118 communication for an EVSE.

The script binds both the SLAC controller and ISO 15118 SECC directly to
an existing network interface (e.g. ``eth0``). Once a successful SLAC
match occurs, ISO 15118 traffic continues on the same interface.

Command line options allow supplying paths to the certificate store used
by the ISO 15118 stack as well as optional configuration files for both
PySLAC and the SECC implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from pyslac.environment import Config as SlacConfig
from pyslac.session import (
    SlacEvseSession,
    SlacSessionController,
    STATE_MATCHED,
)

from iso15118.secc.secc_settings import Config as SeccConfig
from iso15118.secc.controller.simulator import SimEVSEController
from iso15118.secc.controller.interface import ServiceStatus
from iso15118.secc import SECCHandler
from iso15118.shared.exi_codec import ExificientEXICodec


logger = logging.getLogger("evse.main")


class EVSECommunicationController(SlacSessionController):
    """Handles SLAC matching and starts the ISO 15118 SECC."""

    def __init__(
        self,
        slac_config: SlacConfig,
        secc_config_path: Optional[str] = None,
        certificate_store: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.slac_config = slac_config
        self.secc_config_path = secc_config_path
        self.certificate_store = certificate_store

    async def notify_matching_ongoing(self, evse_id: str) -> None:  # pragma: no cover - logging
        logger.info("SLAC matching in progress for %s", evse_id)

    async def enable_hlc_charging(self, evse_id: str) -> None:  # pragma: no cover - logging
        logger.info("Enabling HLC for EVSE %s", evse_id)

    async def start(self, evse_id: str, iface: str) -> None:
        """Initialise the SLAC session and trigger matching."""
        session = SlacEvseSession(evse_id, iface, self.slac_config)
        await session.evse_set_key()
        await self._trigger_matching(session)

    async def _trigger_matching(self, session: SlacEvseSession) -> None:
        """Simulate CP state transitions to start SLAC and wait for a match."""
        # Move through CP states B -> C to initiate matching
        await self.process_cp_state(session, "B")
        await asyncio.sleep(2)
        await self.process_cp_state(session, "C")

        while session.state != STATE_MATCHED:
            await asyncio.sleep(1)

        logger.info("SLAC match successful, launching ISO 15118 SECC")
        await start_secc(session.iface, self.secc_config_path, self.certificate_store)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evse-id", required=True, help="EVSE identifier used for SLAC")
    parser.add_argument(
        "--slac-config",
        help="Path to PySLAC configuration (.env) file",
    )
    parser.add_argument(
        "--secc-config",
        help="Path to ISO 15118 SECC configuration (.env) file",
    )
    parser.add_argument(
        "--cert-store",
        default=str(Path(__file__).resolve().parents[1] / "pki"),
        help="Directory containing ISO 15118 certificates (PKI_PATH)",
    )
    parser.add_argument(
        "--iface",
        default="eth0",
        help="Network interface used for SLAC and ISO 15118 communication",
    )
    parser.add_argument(
        "--controller",
        choices=["sim", "hal"],
        default="sim",
        help="EVSE controller backend: 'sim' (default) or 'hal' (pluggable hardware)",
    )
    return parser.parse_args()


async def start_secc(
    iface: str,
    secc_config_path: Optional[str],
    certificate_store: Optional[str],
) -> None:
    """Start ISO 15118 SECC bound to *iface*."""
    if certificate_store:
        os.environ["PKI_PATH"] = certificate_store

    logger.info("Starting SECC", extra={"iface": iface})
    config = SeccConfig()
    config.load_envs(secc_config_path)
    config.iface = iface
    try:
        config.print_settings()
    except Exception:
        pass

    controller_mode = os.environ.get("EVSE_CONTROLLER", "sim").lower()
    if controller_mode == "hal":
        # Lazy import to avoid test-time dependency and keep sim default
        from src.evse_hal.registry import create as create_hal
        from src.evse_hal.iso15118_hal_controller import HalEVSEController

        adapter = os.environ.get("EVSE_HAL_ADAPTER", "sim")
        logger.info("EVSE controller=hal", extra={"adapter": adapter})
        evse_controller = HalEVSEController(create_hal(adapter))
    else:
        logger.info("EVSE controller=sim")
        evse_controller = SimEVSEController()
    await evse_controller.set_status(ServiceStatus.STARTING)
    await SECCHandler(
        exi_codec=ExificientEXICodec(),
        evse_controller=evse_controller,
        config=config,
    ).start(config.iface)


def main() -> None:
    # Unified logging setup
    try:
        from src.util.logging import setup_logging
    except Exception:
        from util.logging import setup_logging  # fallback
    setup_logging()
    args = parse_args()
    # Mirror CLI controller choice to environment for downstream components
    if args.controller:
        os.environ["EVSE_CONTROLLER"] = args.controller
    slac_config = SlacConfig()
    slac_config.load_envs(args.slac_config)

    controller = EVSECommunicationController(
        slac_config=slac_config,
        secc_config_path=args.secc_config,
        certificate_store=args.cert_store,
    )

    asyncio.run(controller.start(args.evse_id, args.iface))


if __name__ == "__main__":
    main()
