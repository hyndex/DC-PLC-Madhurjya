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
        """Initialise SLAC and trigger matching.

        - In sim mode, simulate CP B->C transitions.
        - In HAL mode (EVSE_CONTROLLER=hal), monitor CP state from hardware
          and trigger matching on B/C as reported by the CP reader (e.g. ESP).
        """
        controller_mode = os.environ.get("EVSE_CONTROLLER", "sim").lower()
        if controller_mode != "hal":
            session = SlacEvseSession(evse_id, iface, self.slac_config)
            await session.evse_set_key()
            await self._trigger_matching(session)
            self._log_slac_peer(session)
            logger.info("SLAC match successful, launching ISO 15118 SECC")
            await start_secc(iface, self.secc_config_path, self.certificate_store)
            return

        # HAL mode: use real CP input to drive SLAC and ISO lifecycles
        try:
            from src.evse_hal.registry import create as create_hal
        except Exception as e:  # pragma: no cover - runtime only
            logger.error("HAL mode requested but HAL registry unavailable", extra={"error": str(e)})
            return

        adapter = os.environ.get("EVSE_HAL_ADAPTER", "sim")
        hal = create_hal(adapter)
        connected_states = {"B", "C", "D"}
        logger.info("HAL mode: waiting for CP states to start SLAC", extra={"adapter": adapter})

        while True:
            # Wait for vehicle (B/C/D)
            st = hal.cp().get_state()
            if st not in connected_states:
                await asyncio.sleep(0.2)
                continue

            logger.info("Vehicle detected via CP", extra={"cp_state": st})
            session = SlacEvseSession(evse_id, iface, self.slac_config)
            await session.evse_set_key()
            # Feed CP state(s) into SLAC controller
            await self.process_cp_state(session, "B")
            await asyncio.sleep(0.2)
            st = hal.cp().get_state()
            if st in {"C", "D"}:
                await self.process_cp_state(session, "C")

            # Wait for match or disconnect
            while session.state != STATE_MATCHED:
                await asyncio.sleep(0.5)
                st = hal.cp().get_state()
                if st not in connected_states:
                    logger.warning("CP disconnected before SLAC match; restarting", extra={"cp_state": st})
                    break

            if session.state == STATE_MATCHED:
                # Try to extract and log SLAC match details if available
                try:
                    ev_mac = getattr(session, "ev_mac", None)
                    nid = getattr(session, "nid", None)
                    run_id = getattr(session, "run_id", None)
                    attenuation = getattr(session, "attenuation_db", None)
                    logger.info(
                        "SLAC matched",
                        extra={
                            "ev_mac": ev_mac,
                            "nid": nid,
                            "run_id": run_id,
                            "attenuation_db": attenuation,
                        },
                    )
                except Exception:
                    logger.info("SLAC matched (details unavailable)")
                logger.info("Launching ISO 15118 SECC")
                await start_secc(iface, self.secc_config_path, self.certificate_store)
                return

    async def _trigger_matching(self, session: SlacEvseSession) -> None:
        """Simulate CP state transitions to start SLAC and wait for a match."""
        # Move through CP states B -> C to initiate matching
        await self.process_cp_state(session, "B")
        await asyncio.sleep(2)
        await self.process_cp_state(session, "C")

        while session.state != STATE_MATCHED:
            await asyncio.sleep(1)
        # Caller continues to start SECC

    def _log_slac_peer(self, session: SlacEvseSession) -> None:
        """Best-effort logging of EV MAC/NID/RUN_ID from the PySLAC session.

        PySLAC versions expose different attribute names; probe common ones.
        """
        try:
            # Local import to avoid hard dependency during tests
            from src.util.slac_peer_store import write_peer  # type: ignore
        except Exception:
            write_peer = None  # type: ignore
        def _first_attr(obj, names):
            for n in names:
                try:
                    v = getattr(obj, n)
                except Exception:
                    v = None
                if v:
                    return v
            return None

        ev_mac = _first_attr(session, [
            "ev_mac",
            "peer_mac",
            "ev_mac_str",
            "peer_mac_str",
            "ev_mac_addr",
            "peer_mac_addr",
        ])
        nid = _first_attr(session, ["nid", "NID"])  # Network Identifier
        run_id = _first_attr(session, ["run_id", "RUN_ID"])  # SLAC run ID

        try:
            extra = {
                "ev_mac": str(ev_mac) if ev_mac is not None else None,
                "nid": str(nid) if nid is not None else None,
                "run_id": str(run_id) if run_id is not None else None,
            }
            logger.info("SLAC peer info", extra=extra)
        except Exception:
            logger.info("SLAC peer info: ev_mac=%s nid=%s run_id=%s", ev_mac, nid, run_id)

        # Persist for external readers (e.g., API curl)
        try:
            if write_peer:
                write_peer(ev_mac=str(ev_mac) if ev_mac is not None else None,
                           nid=str(nid) if nid is not None else None,
                           run_id=str(run_id) if run_id is not None else None)
        except Exception:
            pass


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
