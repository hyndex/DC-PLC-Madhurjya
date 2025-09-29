from __future__ import annotations

"""Small helper to install uvloop when available.

Usage:
    from util.uvloop_compat import maybe_install_uvloop
    maybe_install_uvloop()

Respects env var USE_UVLOOP (default on). No‑op on unsupported platforms
or when uvloop is not installed.
"""

import os
import sys
import logging

logger = logging.getLogger(__name__)


def _truthy(val: str | None) -> bool:
    if val is None:
        return True
    v = str(val).strip().lower()
    return v not in ("0", "false", "no", "off", "")


def maybe_install_uvloop() -> bool:
    """Install uvloop as the default asyncio policy if possible.

    Returns True if uvloop was installed, else False.
    """
    try:
        if os.name != "posix":  # uvloop is Unix‑only
            return False
        if not _truthy(os.environ.get("USE_UVLOOP", "1")):
            return False
        # Avoid re‑installing if a loop already exists
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop and loop.is_running():
                return False
        except Exception:
            pass
        import uvloop  # type: ignore

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.info("uvloop installed as default event loop policy")
        return True
    except Exception as exc:  # pragma: no cover - best effort
        # Be quiet if uvloop not present; otherwise log once at DEBUG
        if isinstance(exc, ModuleNotFoundError):
            return False
        try:
            logger.debug("uvloop install skipped: %s", exc)
        except Exception:
            pass
        return False

