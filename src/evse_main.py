#!/usr/bin/env python3
"""Convenience script to start SLAC and ISO 15118 communication for an EVSE.

This module bridges the QCA7000 PLC modem to a TAP interface, runs the
PySLAC library in EVSE mode and, once a successful SLAC match occurs,
starts the ISO 15118 Supply Equipment Communication Controller (SECC).

The script exposes command line options for providing paths to the
certificate store used by the ISO 15118 stack as well as to optional
configuration files for both PySLAC and the SECC implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
from typing import Optional

from plc_communication.plc_network import PLCNetwork
from plc_communication.plc_to_tap import (
    configure_tap_interface,
    create_tap_interface,
    plc_to_tap,
    tap_to_plc,
)

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


logger = logging.getLogger(__name__)


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
        help="Directory containing ISO 15118 certificates (PKI_PATH)",
    )
    parser.add_argument(
        "--iface-ip",
        default="192.168.1.1",
        help="IPv4 address assigned to the TAP interface",
    )
    parser.add_argument(
        "--iface-netmask",
        default="24",
        help="Netmask for the TAP interface",
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

    config = SeccConfig()
    config.load_envs(secc_config_path)
    config.iface = iface
    config.print_settings()

    evse_controller = SimEVSEController()
    await evse_controller.set_status(ServiceStatus.STARTING)
    await SECCHandler(
        exi_codec=ExificientEXICodec(),
        evse_controller=evse_controller,
        config=config,
    ).start(config.iface)


def start_plc_bridge(ip_address: str, netmask: str) -> str:
    """Initialise the PLC ↔ TAP bridge and return the interface name."""
    tap_fd, tap_name = create_tap_interface()
    configure_tap_interface(tap_name, ip_address, netmask)
    plc = PLCNetwork()

    threading.Thread(target=plc_to_tap, args=(plc, tap_fd), daemon=True).start()
    threading.Thread(target=tap_to_plc, args=(plc, tap_fd), daemon=True).start()
    return tap_name


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    tap_name = start_plc_bridge(args.iface_ip, args.iface_netmask)

    slac_config = SlacConfig()
    slac_config.load_envs(args.slac_config)

    controller = EVSECommunicationController(
        slac_config=slac_config,
        secc_config_path=args.secc_config,
        certificate_store=args.cert_store,
    )

    asyncio.run(controller.start(args.evse_id, tap_name))


if __name__ == "__main__":
    main()

