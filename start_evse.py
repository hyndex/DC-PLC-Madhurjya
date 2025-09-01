#!/usr/bin/env python3
"""Start the ISO 15118 SECC using configuration from src/iso15118/.env."""

import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent

try:
    # Ensure local 'src' (siblings of this file's parent) takes precedence
    ROOT = Path(__file__).resolve().parent
    SRC_DIR = ROOT / "src"
    LOCAL_ISO15118_ROOT = SRC_DIR / "iso15118"
    if (LOCAL_ISO15118_ROOT / "iso15118" / "__init__.py").is_file():
        p = str(LOCAL_ISO15118_ROOT)
        if p not in sys.path:
            sys.path.insert(0, p)
    from iso15118.secc import SECCHandler  # type: ignore
    from iso15118.secc.controller.simulator import SimEVSEController  # type: ignore
    from iso15118.secc.controller.interface import ServiceStatus  # type: ignore
    from iso15118.secc.secc_settings import Config  # type: ignore
    from iso15118.shared.exificient_exi_codec import ExificientEXICodec  # type: ignore
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    msg = (
        "The 'iso15118' package is required. Install it using 'pip install iso15118'."
    )
    logger.error(msg)
    raise ModuleNotFoundError(msg) from exc

async def main() -> None:
    """Load configuration and start SECC."""
    env_path = ROOT / "src/iso15118/.env"
    config = Config()
    config.load_envs(str(env_path))
    config.print_settings()

    evse_controller = SimEVSEController()
    await evse_controller.set_status(ServiceStatus.STARTING)
    await SECCHandler(
        exi_codec=ExificientEXICodec(),
        evse_controller=evse_controller,
        config=config,
    ).start(config.iface)


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.debug("SECC program terminated manually")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
