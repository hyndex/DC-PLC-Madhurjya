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
import fcntl
import logging
import os
import sys
from pathlib import Path
import signal
import time
import subprocess
from typing import Optional

# Ensure local 'src' (this directory) is importable so subpackages like
# 'util' can be imported as top-level modules when running as a module
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Make local PySLAC importable without requiring external installation
try:
    from pyslac.environment import Config as SlacConfig
    # PySLAC exposes state constants in enums; some test stubs only provide a subset.
    from pyslac.session import SlacEvseSession, SlacSessionController  # type: ignore
    try:
        # Prefer import from enums when available
        from pyslac.enums import STATE_MATCHED  # type: ignore
    except Exception:  # pragma: no cover - test stubs
        # Fallback when tests stub pyslac.session only
        from pyslac.session import STATE_MATCHED  # type: ignore
except ModuleNotFoundError:
    _PYSLAC_BASE = _SRC_DIR / "pyslac"
    if (_PYSLAC_BASE / "pyslac" / "__init__.py").is_file():
        sys.path.insert(0, str(_PYSLAC_BASE))
    # Retry import after adjusting path; if it fails again, let it raise
    from pyslac.environment import Config as SlacConfig
    from pyslac.session import SlacEvseSession, SlacSessionController  # type: ignore
    try:
        from pyslac.enums import STATE_MATCHED  # type: ignore
    except Exception:  # pragma: no cover - test stubs
        from pyslac.session import STATE_MATCHED  # type: ignore

# Ensure local 'src' takes precedence for iso15118 imports
HERE = Path(__file__).resolve().parent
# The iso15118 package here lives under a nested src layout: src/iso15118/iso15118
LOCAL_ISO15118_ROOT = HERE / "iso15118"
if (LOCAL_ISO15118_ROOT / "iso15118" / "__init__.py").is_file():
    p = str(LOCAL_ISO15118_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

# Ensure local PySLAC (src/pyslac/pyslac) is importable without requiring PYTHONPATH
LOCAL_PYSLAC_ROOT = HERE / "pyslac"
if (LOCAL_PYSLAC_ROOT / "pyslac" / "__init__.py").is_file():
    p = str(LOCAL_PYSLAC_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

from iso15118.secc.secc_settings import Config as SeccConfig
from iso15118.secc.controller.simulator import SimEVSEController
from iso15118.secc.controller.interface import ServiceStatus
from iso15118.secc import SECCHandler
try:
    from iso15118.shared.exi_codec import ExificientEXICodec, EXI
except ImportError:  # pragma: no cover - allow minimal stubs for unit tests
    from iso15118.shared.exi_codec import ExificientEXICodec  # type: ignore

    class _DummyEXI:
        def set_exi_codec(self, _codec) -> None:
            return None

        def to_exi(self, *_args, **_kwargs):
            return b""

    def EXI():  # type: ignore
        return _DummyEXI()
try:
    from iso15118.shared.messages.app_protocol import (
        SupportedAppProtocolRes,
        ResponseCodeSAP,
    )
    from iso15118.shared.messages.enums import Namespace
    from iso15118.shared.messages.iso15118_2.body import SessionSetupRes
    from iso15118.shared.messages.iso15118_2.header import MessageHeader
    from iso15118.shared.messages.iso15118_2.msgdef import V2GMessage as V2GMessageV2
    from iso15118.shared.messages.din_spec.body import SessionSetupRes as SessionSetupResDIN
    from iso15118.shared.messages.din_spec.header import MessageHeader as MessageHeaderDIN
    from iso15118.shared.messages.din_spec.msgdef import V2GMessage as V2GMessageDIN
except Exception:  # pragma: no cover - minimal placeholders for unit tests
    class ResponseCodeSAP(str):
        NEGOTIATION_OK = "NEGOTIATION_OK"

    class SupportedAppProtocolRes:  # type: ignore
        def __init__(self, response_code: str, schema_id: int):
            self.response_code = response_code
            self.schema_id = schema_id

    class Namespace(str):
        SAP = "SAP"
        ISO_V2_MSG_DEF = "ISO_V2_MSG_DEF"
        DIN_MSG_DEF = "DIN_MSG_DEF"

    class SessionSetupRes:  # type: ignore
        def __init__(self, evse_id: str):
            self.evse_id = evse_id

    class MessageHeader:  # type: ignore
        def __init__(self, session_id: str):
            self.session_id = session_id

    class V2GMessageV2:  # type: ignore
        def __init__(self, header: MessageHeader, body: dict):
            self.header = header
            self.body = body

    SessionSetupResDIN = SessionSetupRes  # type: ignore
    MessageHeaderDIN = MessageHeader  # type: ignore

    class V2GMessageDIN(V2GMessageV2):  # type: ignore
        pass
try:
    from util.standards_check import log_timing_summary
except Exception:
    # Fallback when 'src' is used as package root
    from src.util.standards_check import log_timing_summary  # type: ignore


logger = logging.getLogger("evse.main")


def _acquire_process_lock(lock_path: Optional[str] = None):
    """Ensure a single EVSE process instance holds the lock.

    If a prior instance is detected and EVSE_LOCK_STEAL is enabled (default 1),
    attempt a graceful takeover by signaling the previous process and retrying
    the lock for a short window.
    """
    target = Path(lock_path or os.environ.get("EVSE_LOCK_PATH", "/tmp/evse_main.lock"))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _try_lock(fp) -> bool:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    # Open existing or create new
    fp = open(target, "a+")
    if _try_lock(fp):
        fp.seek(0)
        fp.truncate(0)
        fp.write(f"{os.getpid()}\n")
        fp.flush()
        return fp

    # Locked by another process – optionally try to steal
    steal_env = os.environ.get("EVSE_LOCK_STEAL", "1").strip().lower()
    allow_steal = steal_env not in ("0", "false", "no", "off", "")
    if allow_steal:
        # Read candidate PID
        pid = None
        try:
            fp.seek(0)
            content = fp.read().strip()
            if content:
                pid = int(content.splitlines()[0].strip())
        except Exception:
            pid = None
        # Validate PID and attempt graceful shutdown
        def _pid_alive(p: int) -> bool:
            return p > 0 and os.path.exists(f"/proc/{p}")

        if pid and _pid_alive(pid):
            # Try TERM then KILL with backoff
            for sig, dwell in ((signal.SIGTERM, 1.0), (signal.SIGKILL, 0.5)):
                try:
                    os.kill(pid, sig)
                except Exception:
                    pass
                # retry lock during dwell
                deadline = time.time() + dwell
                while time.time() < deadline:
                    time.sleep(0.05)
                    if _try_lock(fp):
                        fp.seek(0)
                        fp.truncate(0)
                        fp.write(f"{os.getpid()}\n")
                        fp.flush()
                        return fp
                # if process already died, try once more immediately
                if not _pid_alive(pid):
                    if _try_lock(fp):
                        fp.seek(0)
                        fp.truncate(0)
                        fp.write(f"{os.getpid()}\n")
                        fp.flush()
                        return fp

        # If PID is missing (stale lock), retry acquiring a few times
        if not pid or not _pid_alive(pid):
            for _ in range(10):
                time.sleep(0.05)
                if _try_lock(fp):
                    fp.seek(0)
                    fp.truncate(0)
                    fp.write(f"{os.getpid()}\n")
                    fp.flush()
                    return fp

    # Could not acquire
    fp.close()
    raise RuntimeError(
        f"Another EVSE instance appears to be running (lock file: {target})."
    )


def _release_process_lock(fp) -> None:
    if not fp:
        return
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fp.close()
    except Exception:
        pass


def _resolve_iface_driver_name(iface: str) -> Optional[str]:
    try:
        driver_link = Path("/sys/class/net") / iface / "device" / "driver"
        if not driver_link.exists():
            return None
        return driver_link.resolve().name
    except Exception:
        return None


def _has_ipv6_link_local(iface: str) -> bool:
    try:
        with open("/proc/net/if_inet6", "r", encoding="utf-8") as fd:
            for line in fd:
                parts = line.split()
                if len(parts) >= 6 and parts[5] == iface and parts[0].startswith("fe80"):
                    return True
    except FileNotFoundError:
        pass
    return False


def _log_plc_interface_health(iface: str) -> None:
    driver = _resolve_iface_driver_name(iface)
    if driver:
        logger.info("PLC interface detected", extra={"iface": iface, "driver": driver})
        allowed = {"qca7000", "qcaspi"}
        if driver not in allowed:
            logger.warning(
                "PLC interface driver not in preferred list",
                extra={"iface": iface, "driver": driver, "expected": sorted(allowed)},
            )
    else:
        logger.warning("Unable to resolve PLC driver", extra={"iface": iface})
    if not _has_ipv6_link_local(iface):
        logger.warning(
            "PLC interface lacks IPv6 link-local address",
            extra={"iface": iface, "hint": "ensure startup script ran ensure_lladdr"},
        )


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
        try:
            _log_plc_interface_health(iface)
        except Exception:
            logger.debug("PLC interface health check failed", exc_info=True)
        if controller_mode != "hal":
            session = SlacEvseSession(evse_id, iface, self.slac_config)
            # Defer CM_SET_KEY until after SLAC starts to maximize compatibility
            await self._trigger_matching(session)
            self._log_slac_peer(session)
            logger.info("SLAC match successful, launching ISO 15118 SECC")
            await start_secc(iface, self.secc_config_path, self.certificate_store)
            return

        # HAL mode: use real CP input to drive SLAC and ISO lifecycles
        try:
            from src.evse_hal.registry import create as create_hal
        except Exception as e1:  # pragma: no cover - runtime only
            try:
                from evse_hal.registry import create as create_hal  # fallback when run as package root
            except Exception as e2:
                logger.error(
                    "HAL mode requested but HAL registry unavailable",
                    extra={"error": f"{e1}; {e2}"},
                )
                return

        adapter = os.environ.get("EVSE_HAL_ADAPTER", "sim")
        try:
            hal = create_hal(adapter)
        except Exception as e:
            logger.error("HAL adapter init failed", extra={"adapter": adapter, "error": str(e)})
            return
        connected_states = {"B", "C", "D"}
        emergency_states = {"E", "F"}
        logger.info("HAL mode: waiting for CP states to start SLAC", extra={"adapter": adapter})

        # Lifecycle variables
        keyed_once = False
        session: Optional[SlacEvseSession] = None
        session_started_at: float = 0.0
        secc_task: Optional[asyncio.Task] = None
        secc_handler = None  # type: ignore
        last_cp: Optional[str] = None
        # Track last CP letter forwarded to PySLAC so we propagate transitions
        last_slac_cp_forwarded: Optional[str] = None
        # CM_SET_KEY failure counter for proactive PLC soft reset
        cm_set_key_fail_count: int = 0
        # SLAC init retry control per plug-in
        # Allow more retries by default for field robustness
        try:
            max_slac_attempts = int(os.environ.get("SLAC_MAX_ATTEMPTS", "5"))
        except Exception:
            max_slac_attempts = 5
        try:
            slac_retry_backoff_s = float(os.environ.get("SLAC_RETRY_BACKOFF_S", "1.5"))
        except Exception:
            slac_retry_backoff_s = 1.5
        slac_attempts = 0
        # Backoff between CM_SET_KEY attempts
        try:
            setkey_backoff_s = float(os.environ.get("SLAC_SETKEY_RETRY_BACKOFF_S", "0.5"))
        except Exception:
            setkey_backoff_s = 0.5
        last_setkey_ts: float = 0.0
        # Log SLAC peer at first sight of EV MAC even before MATCHED
        ev_peer_logged = False
        # Gentle nudge control to coax PEV to restart SLAC sooner when stuck at B
        try:
            first_nudge_s = float(os.environ.get("SLAC_FIRST_NUDGE_S", "6.0"))
        except Exception:
            first_nudge_s = 6.0
        try:
            nudge_every_s = float(os.environ.get("SLAC_NUDGE_EVERY_S", "12.0"))
        except Exception:
            nudge_every_s = 12.0
        last_nudge_ts: float = 0.0

        # Whether Pi is allowed to hint CP via ESP (mode/pwm toggles). Default disabled.
        def _cp_host_hints_enabled() -> bool:
            try:
                v = os.environ.get("EVSE_CP_HOST_HINTS", "0").strip().lower()
                return v not in ("0", "false", "no", "off", "")
            except Exception:
                return False

        async def _start_secc_bg() -> None:
            nonlocal secc_task, secc_handler
            if secc_task is not None:
                return
            logger.info("Launching ISO 15118 SECC")
            # Reuse the same HAL instance to avoid double-opening the ESP UART
            secc_handler, secc_task = await launch_secc_background(
                iface, self.secc_config_path, self.certificate_store, existing_hal=hal
            )

        async def _stop_secc(reason: str = "CP disconnect") -> None:
            nonlocal secc_task, secc_handler
            if secc_task is None:
                return
            try:
                try:
                    getattr(secc_handler, "close_session", lambda: None)()
                except Exception:
                    pass
                secc_task.cancel()
                try:
                    await asyncio.wait_for(secc_task, timeout=2.0)
                except Exception:
                    pass
            finally:
                secc_task = None
                secc_handler = None
                logger.info("SECC stopped", extra={"reason": reason})

        async def _ensure_locked_before_plc() -> bool:
            """If a cable lock exists, enforce lock before PLC starts.

            Returns True if either locked or no lock present/required.
            """
            # Discover optional cable lock driver
            lock = getattr(hal, "cable_lock", None)
            if callable(lock):
                lock = lock()
            if not lock:
                return True
            # Config: enforce lock by default if lock hardware is present
            enforce = os.environ.get("CABLE_LOCK_ENFORCE", "1").strip() not in ("0", "false", "no")
            if not enforce:
                return True
            # Already locked?
            is_locked = getattr(lock, "is_locked", lambda: None)()
            if is_locked:
                return True
            try:
                lock.lock()
            except Exception:
                # If lock actuation fails and enforcement is strict, do not proceed
                return False
            # Verify lock state with timeout
            try:
                verify_s = float(os.environ.get("CABLE_LOCK_VERIFY_TIMEOUT_S", "1.0"))
            except Exception:
                verify_s = 1.0
            deadline = asyncio.get_event_loop().time() + max(0.0, verify_s)
            while asyncio.get_event_loop().time() < deadline:
                ok = getattr(lock, "is_locked", lambda: True)()
                if ok:
                    return True
                await asyncio.sleep(0.02)
            return False

        async def _unlock_cable_best_effort(reason: str) -> None:
            lock = getattr(hal, "cable_lock", None)
            if callable(lock):
                lock = lock()
            allow = os.environ.get("CABLE_UNLOCK_ON_FAULT", "1").strip() not in ("0", "false", "no")
            if lock and allow:
                try:
                    lock.unlock()
                    logger.info("Cable unlocked", extra={"reason": reason})
                except Exception:
                    pass

        async def _plc_soft_reset_proactive() -> None:
            """Optionally perform a PLC soft reset (qcaspi rebind) when SLAC setup fails.

            Controlled by EVSE_PLC_AUTO_SOFT_RESET (default 1). Tunables:
              - EVSE_PLC_AUTO_SOFT_RESET_SPEED (Hz, e.g., 8000000)
              - EVSE_PLC_AUTO_SOFT_RESET_BURST (e.g., 3000)
              - QCASPI_PLUGGABLE (default 1)
            """
            try:
                if os.environ.get("EVSE_PLC_AUTO_SOFT_RESET", "1").strip().lower() in ("0", "false", "no", "off", ""):
                    return
                env = os.environ.copy()
                if env.get("EVSE_PLC_AUTO_SOFT_RESET_SPEED"):
                    env["QCASPI_CLKSPEED"] = env["EVSE_PLC_AUTO_SOFT_RESET_SPEED"]
                if env.get("EVSE_PLC_AUTO_SOFT_RESET_BURST"):
                    env["QCASPI_BURST"] = env["EVSE_PLC_AUTO_SOFT_RESET_BURST"]
                env.setdefault("QCASPI_PLUGGABLE", env.get("QCASPI_PLUGGABLE", "1"))
                repo_root = Path(__file__).resolve().parents[1]
                script = repo_root / "scripts" / "plc_soft_reset.sh"
                if script.is_file():
                    proc = await asyncio.create_subprocess_exec(
                        "bash", str(script), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env
                    )
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=6.0)
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                await asyncio.sleep(0.3)
            except Exception:
                pass

        def _ensure_ipv6_ll_and_flags(ifname: str) -> None:
            """Ensure fe80::/64 link‑local and rx flags on a candidate PLC iface.

            Safe no‑op if already configured. Requires root privileges (start script runs with sudo -E).
            """
            try:
                # Check if LL present
                has_ll = False
                with open("/proc/net/if_inet6", "r", encoding="utf-8") as fd:
                    for line in fd:
                        parts = line.split()
                        if len(parts) >= 6 and parts[5] == ifname and parts[0].startswith("fe80"):
                            has_ll = True
                            break
                if not has_ll:
                    # Compute EUI-64 from MAC
                    mac_path = Path("/sys/class/net") / ifname / "address"
                    mac_txt = mac_path.read_text(encoding="utf-8").strip().lower()
                    o = [int(x, 16) for x in mac_txt.split(":")]
                    o[0] ^= 0x02  # flip U/L bit
                    eui = [o[0], o[1], o[2], 0xFF, 0xFE, o[3], o[4], o[5]]
                    ll = "fe80::{:02x}{:02x}:{:02x}{:02x}:{:02x}{:02x}:{:02x}{:02x}".format(*eui)
                    # Bring up and add LL
                    subprocess.run(["ip", "link", "set", ifname, "up"], check=False)
                    subprocess.run(["ip", "-6", "addr", "add", f"{ll}/64", "dev", ifname, "scope", "link"], check=False)
                # Enable flags helpful for SLAC capture
                subprocess.run(["ip", "link", "set", ifname, "promisc", "on", "multicast", "on", "allmulticast", "on"], check=False)
            except Exception:
                pass

        async def _teardown_session(reason: str) -> None:
            nonlocal session, session_started_at, last_slac_cp_forwarded, ev_peer_logged, slac_attempts, keyed_once
            if session is None:
                return
            logger.debug("Tearing down SLAC session", extra={"reason": reason})
            try:
                await self.process_cp_state(session, "A")
            except Exception:
                pass
            try:
                await session.leave_logical_network()
            except Exception:
                pass
            try:
                session.close()
            except Exception:
                pass
            session = None
            session_started_at = 0.0
            last_slac_cp_forwarded = None
            ev_peer_logged = False
            slac_attempts = 0
            keyed_once = False

        try:
            while True:
                try:
                    cp = hal.cp().get_state()
                except Exception:
                    cp = None
    
                if cp != last_cp:
                    logger.debug("CP transition", extra={"from": last_cp, "to": cp})
                    last_cp = cp
                    # If a SLAC session is active, forward CP transitions promptly
                    if session is not None and cp is not None:
                        # Map HAL letters to SLAC controller states (D treated as C)
                        slac_cp = "C" if cp in {"C", "D"} else (cp if cp in {"A", "B"} else None)
                        if slac_cp and slac_cp != last_slac_cp_forwarded:
                            try:
                                await self.process_cp_state(session, slac_cp)
                                last_slac_cp_forwarded = slac_cp
                            except Exception:
                                pass
    
                # Emergency states: cut power and unlock immediately
                if cp in emergency_states:
                    try:
                        hal.contactor().set_closed(False)
                    except Exception:
                        pass
                    await _unlock_cable_best_effort("cp_emergency")
                    # Stop SECC quickly
                    if secc_task is not None:
                        await _stop_secc("CP emergency state")
                    # Reset any SLAC session state
                    await _teardown_session("cp_emergency")
                    # Hint firmware CP to safe if available
                    if _cp_host_hints_enabled():
                        try:
                            getattr(hal, "esp_set_mode", lambda _m=None: None)("manual")
                            getattr(hal, "esp_set_pwm", lambda _d, enable=True: None)(100, True)
                        except Exception:
                            pass
                        # Restore dc mode so CP reports 5% duty when reconnected
                        try:
                            getattr(hal, "esp_set_mode", lambda _m=None: None)("dc")
                        except Exception:
                            pass
                    # Allow fresh SetKey on next connection
    
                elif cp in connected_states:
                    if session is None:
                        if slac_attempts >= max_slac_attempts:
                            # Exhausted attempts; wait for CP disconnect or manual retry
                            if int(asyncio.get_event_loop().time() * 10) % 10 == 0:
                                logger.warning(
                                    "SLAC attempts exhausted (max=%d); holding until CP disconnect",
                                    max_slac_attempts,
                                )
                            await asyncio.sleep(0.5)
                            continue
                        # Ensure plug is fully seated and (optionally) locked before PLC
                        # Small stability wait for CP to avoid starting on a glitch
                        try:
                            stable_s = float(os.environ.get("CP_STABLE_BEFORE_START_S", "0.1"))
                        except Exception:
                            stable_s = 0.1
                        if stable_s > 0:
                            t0 = asyncio.get_event_loop().time()
                            ok = True
                            while asyncio.get_event_loop().time() - t0 < stable_s:
                                try:
                                    if hal.cp().get_state() not in connected_states:
                                        ok = False
                                        break
                                except Exception:
                                    ok = False
                                    break
                                await asyncio.sleep(0.02)
                            if not ok:
                                await asyncio.sleep(0.05)
                                continue
    
                        # Try to engage cable lock if present/enforced
                        locked_ok = await _ensure_locked_before_plc()
                        if not locked_ok:
                            logger.warning("Cable lock not confirmed; deferring PLC start")
                            await asyncio.sleep(0.2)
                            continue
    
                        logger.info("Vehicle detected via CP", extra={"cp_state": cp})
                        # Try initial iface then fall back to common PLC ifaces if SetKey fails
                        candidate_ifaces = []
                        try:
                            # Start with provided iface
                            seen = set()
                            for name in [iface, "plc0", "eth1", "eth0"]:
                                if name and name not in seen and os.path.isdir(f"/sys/class/net/{name}"):
                                    candidate_ifaces.append(name)
                                    seen.add(name)
                        except Exception:
                            candidate_ifaces = [iface]

                        session = None
                        last_slac_cp_forwarded = None
                        # Attempt SetKey on candidates honoring backoff
                        if not keyed_once:
                            now = asyncio.get_event_loop().time()
                            if (now - last_setkey_ts) < max(0.0, setkey_backoff_s):
                                await asyncio.sleep(max(0.0, setkey_backoff_s - (now - last_setkey_ts)))
                            last_setkey_ts = asyncio.get_event_loop().time()

                            setkey_ok = False
                            last_err: Optional[Exception] = None
                            for ifc_try in candidate_ifaces:
                                # Ensure IPv6 link‑local and NIC flags on this iface
                                _ensure_ipv6_ll_and_flags(ifc_try)
                                try:
                                    # Log health and attempt on this iface
                                    try:
                                        _log_plc_interface_health(ifc_try)
                                    except Exception:
                                        pass
                                    sess_try = SlacEvseSession(evse_id, ifc_try, self.slac_config)
                                    await sess_try.evse_set_key()
                                    # Success on this iface
                                    session = sess_try
                                    iface = ifc_try
                                    keyed_once = True
                                    setkey_ok = True
                                    logger.info("CM_SET_KEY succeeded", extra={"iface": iface})
                                    break
                                except Exception as e:
                                    last_err = e
                                    logger.warning("CM_SET_KEY failed on iface", extra={"iface": ifc_try, "error": str(e)})
                                    # Try next candidate
                                    continue
                            if not setkey_ok:
                                cm_set_key_fail_count += 1
                                if cm_set_key_fail_count == 1:
                                    await _plc_soft_reset_proactive()
                                # Defer retry to next loop iteration
                                logger.warning(
                                    "CM_SET_KEY failed; will retry",
                                    extra={"attempt": cm_set_key_fail_count, "last_error": str(last_err) if last_err else None},
                                )
                                await asyncio.sleep(slac_retry_backoff_s)
                                continue
                        # If SetKey already done earlier, create session bound to current iface
                        if session is None:
                            session = SlacEvseSession(evse_id, iface, self.slac_config)
                        await self.process_cp_state(session, "B")
                        last_slac_cp_forwarded = "B"
                        await asyncio.sleep(0.2)
                        cur = hal.cp().get_state()
                        if cur in {"C", "D"}:
                            await self.process_cp_state(session, "C")
                            last_slac_cp_forwarded = "C"
                        session_started_at = asyncio.get_event_loop().time()
    
                    if session and session.state == STATE_MATCHED and secc_task is None:
                        try:
                            self._log_slac_peer(session)
                        except Exception:
                            pass
                        await _start_secc_bg()
    
                    if session and session.state != STATE_MATCHED:
                        # No-op here; CM_SET_KEY already handled above with backoff
                        # Proactive nudge if we appear stuck in CP=B after initial setup
                        try:
                            now = asyncio.get_event_loop().time()
                            if cp == "B" and session_started_at > 0 and (now - session_started_at) >= max(0.0, first_nudge_s):
                                if (now - last_nudge_ts) >= max(0.0, nudge_every_s):
                                    if _cp_host_hints_enabled():
                                        try:
                                            reset_ms = int(os.environ.get("SLAC_RESTART_HINT_MS", "400"))
                                        except Exception:
                                            reset_ms = 400
                                        # Choose AC or DC nudge based on configured CP mode
                                        cp_mode = os.environ.get("ESP_CP_MODE", os.environ.get("EVSE_CP_MODE", "dc")).strip().lower()
                                        try:
                                            if cp_mode in ("ac", "manual"):
                                                getattr(hal, "ac_hlc_nudge", lambda _ms=None: None)(reset_ms)
                                            else:
                                                getattr(hal, "restart_slac_hint", lambda _ms=None: None)(reset_ms)
                                            last_nudge_ts = now
                                            logger.info("HAL SLAC proactive nudge", extra={"reset_ms": reset_ms, "cp_mode": cp_mode})
                                        except Exception:
                                            pass
                        except Exception:
                            pass
                        # Keep SLAC session informed of steady-state CP even if it hasn't changed recently
                        try:
                            slac_cp = (
                                "C" if cp in {"C", "D"} else (cp if cp in {"A", "B"} else None)
                            )
                            if session is not None and slac_cp and slac_cp != last_slac_cp_forwarded:
                                await self.process_cp_state(session, slac_cp)
                                last_slac_cp_forwarded = slac_cp
                        except Exception:
                            pass
                        # If EV MAC is known (after SLAC_PARM), log once early
                        try:
                            if not ev_peer_logged and getattr(session, "pev_mac", None):
                                self._log_slac_peer(session)
                                ev_peer_logged = True
                        except Exception:
                            pass
                        elapsed = asyncio.get_event_loop().time() - session_started_at
                        env_wait = os.environ.get("SLAC_WAIT_TIMEOUT_S")
                        timeout_s = (
                            float(env_wait)
                            if env_wait is not None
                            else float(self.slac_config.slac_init_timeout or 50.0)
                        )
                        if elapsed > timeout_s:
                            slac_attempts += 1
                            logger.warning(
                                "SLAC match timeout (attempt %d/%d); applying restart hint",
                                slac_attempts,
                                max_slac_attempts,
                            )
                            if _cp_host_hints_enabled():
                                try:
                                    reset_ms = int(os.environ.get("SLAC_RESTART_HINT_MS", "400"))
                                    getattr(hal, "restart_slac_hint", lambda _ms=None: None)(reset_ms)
                                    logger.info(
                                        "HAL SLAC restart hint requested",
                                        extra={"reset_ms": reset_ms, "iface": iface, "timeout_s": timeout_s},
                                    )
                                except Exception:
                                    pass
                            # Gracefully reset SLAC state on the current session
                            await _teardown_session("slac_timeout")
                            # If attempts remain, back off briefly before next try
                            if slac_attempts < max_slac_attempts:
                                try:
                                    await asyncio.sleep(slac_retry_backoff_s)
                                except Exception:
                                    pass
                            else:
                                # Too many failures; optionally auto-restart matching without requiring a disconnect
                                auto_restart = os.environ.get("SLAC_AUTO_RESTART", "1").strip().lower() not in ("0", "false", "no")
                                if auto_restart and (cp in connected_states):
                                    logger.warning(
                                        "SLAC failed after %d attempts; auto-restarting after backoff",
                                        slac_attempts,
                                    )
                                    # Proactive nudge before retrying
                                    try:
                                        reset_ms = int(os.environ.get("SLAC_RESTART_HINT_MS", "400"))
                                    except Exception:
                                        reset_ms = 400
                                    if _cp_host_hints_enabled():
                                        try:
                                            getattr(hal, "restart_slac_hint", lambda _ms=None: None)(reset_ms)
                                            logger.info("HAL SLAC proactive nudge (auto-restart)", extra={"reset_ms": reset_ms})
                                        except Exception:
                                            pass
                                    # Reset attempt counter and wait before next try
                                    slac_attempts = 0
                                    try:
                                        await asyncio.sleep(max(0.2, slac_retry_backoff_s))
                                    except Exception:
                                        pass
                                    session_started_at = 0.0
                                    continue
                                else:
                                    # Surface an error and wait for CP disconnect/replug
                                    logger.error(
                                        "SLAC initialization failed after %d attempts; waiting for CP disconnect/retry",
                                        slac_attempts,
                                    )
                                    # Block further attempts until CP disconnect resets the counter
                else:
                    # Safety first: immediately open contactor on CP disconnect
                    # (host-side cutoff). Default 100 ms to align with IEC 61851.
                    try:
                        cutoff_s = float(os.environ.get("SECC_CP_DISCONNECT_IMMEDIATE_CUTOFF_S", "0.1"))
                    except Exception:
                        cutoff_s = 0.1
                    if cutoff_s > 0:
                        try:
                            hal.contactor().set_closed(False)
                            if _cp_host_hints_enabled():
                                # Attempt to drive CP to a safe state as a hardware hint
                                getattr(hal, "esp_set_mode", lambda _m=None: None)("manual")
                                getattr(hal, "esp_set_pwm", lambda _d, enable=True: None)(100, True)
                        except Exception:
                            pass
                        # Unlock promptly so user can remove connector
                        await _unlock_cable_best_effort("cp_disconnect")
                        # Short delay to satisfy timing without unduly delaying logic
                        try:
                            await asyncio.sleep(min(cutoff_s, 0.2))
                        except Exception:
                            pass
                        # Restore dc mode so EV sees 5% duty once reconnected
                        if _cp_host_hints_enabled():
                            try:
                                getattr(hal, "esp_set_mode", lambda _m=None: None)("dc")
                            except Exception:
                                pass
                    # Grace window to tolerate brief CP flaps before tearing down SECC
                    grace_s = float(os.environ.get("CP_DISCONNECT_GRACE_S", "0.5"))
                    if grace_s > 0:
                        await asyncio.sleep(grace_s)
                        try:
                            cp2 = hal.cp().get_state()
                        except Exception:
                            cp2 = None
                        if cp2 in connected_states:
                            # still connected; continue
                            await asyncio.sleep(0.1)
                            continue
                    if secc_task is not None:
                        await _stop_secc("CP state not connected")
                    await _teardown_session("cp_disconnect")
                    # Optional: nudge SLAC reset hint on disconnect
                    try:
                        ms = int(os.environ.get("SLAC_RESTART_ON_DISCONNECT_MS", "0"))
                        if ms > 0 and _cp_host_hints_enabled():
                            getattr(hal, "restart_slac_hint", lambda _ms=None: None)(ms)
                            logger.info("HAL SLAC restart hint on disconnect", extra={"reset_ms": ms})
                    except Exception:
                        pass
    
                # Adaptive polling: faster while connected/charging to cut latency
                base_sleep = 0.2
                try:
                    fast_connected = float(os.environ.get("CP_POLL_CONNECTED_S", "0.05"))
                except Exception:
                    fast_connected = 0.05
                try:
                    fastest_emergency = float(os.environ.get("CP_POLL_EMERGENCY_S", "0.02"))
                except Exception:
                    fastest_emergency = 0.02
                if cp in emergency_states:
                    await asyncio.sleep(max(0.0, fastest_emergency))
                elif cp in connected_states or (secc_task is not None):
                    await asyncio.sleep(max(0.0, fast_connected))
                else:
                    await asyncio.sleep(base_sleep)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("HAL controller loop crashed")
            raise
        finally:
            try:
                if secc_task is not None:
                    await _stop_secc("controller shutdown")
            except Exception:
                pass
            try:
                await _teardown_session("controller shutdown")
            except Exception:
                pass
            try:
                hal.close()
            except Exception as e:
                logger.warning("HAL close failed", extra={"error": str(e)})


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
            "pev_mac",  # PySLAC EV MAC field name
            "ev_mac",
            "peer_mac",
            "ev_mac_str",
            "peer_mac_str",
            "ev_mac_addr",
            "peer_mac_addr",
        ])
        nid = _first_attr(session, ["nid", "NID"])  # Network Identifier
        run_id = _first_attr(session, ["run_id", "RUN_ID"])  # SLAC run ID

        # Normalize bytes to colon-hex for readability
        def _fmt_mac(val):
            if val is None:
                return None
            try:
                b = val if isinstance(val, (bytes, bytearray)) else bytes(val)
                return ":".join(f"{x:02x}" for x in b)
            except Exception:
                return str(val)

        ev_mac_s = _fmt_mac(ev_mac)
        nid_s = _fmt_mac(nid)
        run_id_s = _fmt_mac(run_id)
        # Print in message so it shows with text logging
        logger.info("SLAC peer info: ev_mac=%s nid=%s run_id=%s", ev_mac_s, nid_s, run_id_s)
        # Also attach as structured extras for JSON logs, if enabled
        try:
            logger.debug("SLAC peer info (extra)", extra={
                "ev_mac": ev_mac_s,
                "nid": nid_s,
                "run_id": run_id_s,
            })
        except Exception:
            pass

        # Persist for external readers (e.g., API curl)
        try:
            if write_peer:
                # Persist normalized, human-readable forms to ease external consumption
                write_peer(
                    ev_mac=ev_mac_s,
                    nid=nid_s,
                    run_id=run_id_s,
                )
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
        help="EVSE controller backend: 'sim' or 'hal' (defaults to ENV EVSE_CONTROLLER or 'sim')",
    )
    return parser.parse_args()


async def start_secc(
    iface: str,
    secc_config_path: Optional[str],
    certificate_store: Optional[str],
    existing_hal=None,
) -> None:
    """Start ISO 15118 SECC bound to *iface*."""
    # Pre-flight: ensure the interface has an IPv6 link-local address.
    # This helps avoid sporadic TCP server startup delays/failures.
    try:
        from iso15118.shared.network import validate_nic
        # Retry briefly in case IPv6 config is racing after link-up.
        deadline = asyncio.get_event_loop().time() + 5.0
        while True:
            try:
                validate_nic(iface)
                break
            except Exception:
                if asyncio.get_event_loop().time() > deadline:
                    break
                await asyncio.sleep(0.2)
    except Exception:
        pass
    if certificate_store:
        os.environ["PKI_PATH"] = certificate_store

    logger.info("Starting SECC", extra={"iface": iface})
    config = SeccConfig()
    config.load_envs(secc_config_path)
    config.iface = iface
    # Keep printed settings consistent with runtime override
    try:
        if isinstance(getattr(config, "env_dump", None), dict):
            config.env_dump["NETWORK_INTERFACE"] = iface
    except Exception:
        pass
    # Avoid printing full settings to reduce startup latency

    controller_mode = os.environ.get("EVSE_CONTROLLER", "sim").lower()
    if controller_mode == "hal":
        # Lazy import to avoid test-time dependency and keep sim default
        try:
            from src.evse_hal.registry import create as create_hal
            from src.evse_hal.iso15118_hal_controller import HalEVSEController
        except Exception:
            # Fallback when executed from within src/ (PYTHONPATH=src)
            from evse_hal.registry import create as create_hal  # type: ignore
            from evse_hal.iso15118_hal_controller import HalEVSEController  # type: ignore

        adapter = os.environ.get("EVSE_HAL_ADAPTER", "sim")
        logger.info("EVSE controller=hal", extra={"adapter": adapter})
        # Reuse existing HAL if provided to prevent double-opening UART
        hal_hw = existing_hal if existing_hal is not None else create_hal(adapter)
        evse_controller = HalEVSEController(hal_hw)
    else:
        logger.info("EVSE controller=sim")
        evse_controller = SimEVSEController()
    await evse_controller.set_status(ServiceStatus.STARTING)
    # Pre-warm the EXI codec to avoid multi-second latency on the first
    # encode/decode calls that some EVs interpret as a protocol timeout.
    exi_codec = ExificientEXICodec()
    try:
        # Set the global EXI codec so the warmup hits the same JVM instance
        EXI().set_exi_codec(exi_codec)
        # 1) Warm SAP
        sap_res = SupportedAppProtocolRes(response_code=ResponseCodeSAP.NEGOTIATION_OK, schema_id=0)
        _ = EXI().to_exi(sap_res, Namespace.SAP)
        # 2) Warm ISO 15118-2 minimal SessionSetupRes
        dummy = V2GMessageV2(
            header=MessageHeader(session_id="0" * 16),
            body={"SessionSetupRes": SessionSetupRes(evse_id="DE*PNC*WARMUP*1")}  # type: ignore
        )
        _ = EXI().to_exi(dummy, Namespace.ISO_V2_MSG_DEF)
        # 3) Warm DIN minimal SessionSetupRes
        dummy_din = V2GMessageDIN(
            header=MessageHeaderDIN(session_id="0" * 16),
            body={"SessionSetupRes": SessionSetupResDIN(evse_id="49A89A6360")}  # type: ignore
        )
        _ = EXI().to_exi(dummy_din, Namespace.DIN_MSG_DEF)
    except Exception:
        pass

    handler = SECCHandler(
        exi_codec=exi_codec,
        evse_controller=evse_controller,
        config=config,
    )
    try:
        # Log consolidated timing summary, now with SECC config available
        log_timing_summary(slac_config=None, secc_config=config)
    except Exception:
        pass
    await handler.start(config.iface)


async def launch_secc_background(
    iface: str,
    secc_config_path: Optional[str],
    certificate_store: Optional[str],
    existing_hal=None,
):
    """Start the SECC in a background task and return (handler, task).

    Allows external lifecycle control (stop on CP disconnect) while keeping
    the SECC reusable for new sessions on reconnect.
    """
    if certificate_store:
        os.environ["PKI_PATH"] = certificate_store

    config = SeccConfig()
    config.load_envs(secc_config_path)
    config.iface = iface
    try:
        if isinstance(getattr(config, "env_dump", None), dict):
            config.env_dump["NETWORK_INTERFACE"] = iface
    except Exception:
        pass

    controller_mode = os.environ.get("EVSE_CONTROLLER", "sim").lower()
    if controller_mode == "hal":
        try:
            from src.evse_hal.registry import create as create_hal
            from src.evse_hal.iso15118_hal_controller import HalEVSEController
        except Exception:
            from evse_hal.registry import create as create_hal  # type: ignore
            from evse_hal.iso15118_hal_controller import HalEVSEController  # type: ignore
        adapter = os.environ.get("EVSE_HAL_ADAPTER", "sim")
        logger.info("EVSE controller=hal", extra={"adapter": adapter})
        hal_hw = existing_hal if existing_hal is not None else create_hal(adapter)
        evse_controller = HalEVSEController(hal_hw)
    else:
        logger.info("EVSE controller=sim")
        evse_controller = SimEVSEController()
    await evse_controller.set_status(ServiceStatus.STARTING)

    # Ensure iface readiness as above (short retry window)
    try:
        from iso15118.shared.network import validate_nic
        deadline = asyncio.get_event_loop().time() + 5.0
        while True:
            try:
                validate_nic(iface)
                break
            except Exception:
                if asyncio.get_event_loop().time() > deadline:
                    break
                await asyncio.sleep(0.2)
    except Exception:
        pass

    # Same EXI warmup as in start_secc() to minimize first-response latency
    exi_codec = ExificientEXICodec()
    try:
        EXI().set_exi_codec(exi_codec)
        sap_res = SupportedAppProtocolRes(response_code=ResponseCodeSAP.NEGOTIATION_OK, schema_id=0)
        _ = EXI().to_exi(sap_res, Namespace.SAP)
        dummy = V2GMessageV2(
            header=MessageHeader(session_id="0" * 16),
            body={"SessionSetupRes": SessionSetupRes(evse_id="DE*PNC*WARMUP*1")}  # type: ignore
        )
        _ = EXI().to_exi(dummy, Namespace.ISO_V2_MSG_DEF)
        dummy_din = V2GMessageDIN(
            header=MessageHeaderDIN(session_id="0" * 16),
            body={"SessionSetupRes": SessionSetupResDIN(evse_id="49A89A6360")}  # type: ignore
        )
        _ = EXI().to_exi(dummy_din, Namespace.DIN_MSG_DEF)
    except Exception:
        pass

    handler = SECCHandler(
        exi_codec=exi_codec,
        evse_controller=evse_controller,
        config=config,
    )

    task = asyncio.create_task(handler.start(config.iface))
    return handler, task


def main() -> None:
    # Unified logging setup
    try:
        from src.util.logging import setup_logging
    except Exception:
        from util.logging import setup_logging  # fallback
    setup_logging()
    args = parse_args()
    # Mirror CLI controller choice to environment for downstream components
    # Only override if explicitly provided on the CLI.
    if args.controller is not None:
        os.environ["EVSE_CONTROLLER"] = args.controller
    slac_config = SlacConfig()
    slac_config.load_envs(args.slac_config)

    controller = EVSECommunicationController(
        slac_config=slac_config,
        secc_config_path=args.secc_config,
        certificate_store=args.cert_store,
    )

    # Print consolidated timing summary once before run
    try:
        log_timing_summary(slac_config=slac_config)
    except Exception:
        pass

    lock_fp = None
    try:
        lock_fp = _acquire_process_lock()
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    try:
        try:
            asyncio.run(controller.start(args.evse_id, args.iface))
        except KeyboardInterrupt:
            logger.info("EVSE shutdown requested by signal")
    finally:
        _release_process_lock(lock_fp)


if __name__ == "__main__":
    main()
