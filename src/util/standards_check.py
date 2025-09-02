import logging
from typing import Optional

try:
    # Local import paths inside repo
    from iso15118.shared.messages.timeouts import Timeouts as TShared
    from iso15118.shared.messages.iso15118_2.timeouts import Timeouts as T2
    from iso15118.shared.messages.iso15118_20.timeouts import Timeouts as T20
except Exception:  # pragma: no cover - import-time variability
    TShared = T2 = T20 = None  # type: ignore

logger = logging.getLogger(__name__)


def _sec(val: float) -> str:
    return f"{val:.3f}s"


def log_timing_summary(slac_config=None, secc_config=None) -> None:
    """Log a concise summary of HPGP/ISO15118 timers used at runtime.

    Helps verifying alignment with CCS design guidance without digging
    through code.
    """
    try:
        logger.info("Timing summary (CCS/ISO 15118)")
        if slac_config is not None:
            try:
                init_to = getattr(slac_config, "slac_init_timeout", None)
                atten_to = getattr(slac_config, "slac_atten_results_timeout", None)
                logger.info(
                    "SLAC: init_timeout=%s, atten_results_timeout=%s",
                    _sec(init_to) if init_to is not None else "default",
                    f"{atten_to}ms" if atten_to is not None else "EV-defined (<=1200ms)",
                )
            except Exception:
                pass
        # Static timers from code (should reflect standard)
        if TShared is not None:
            logger.info(
                "ISO15118 Shared: SDP_REQ=%s, SAP_REQ=%s, COMM_SETUP=%s, SEQ_TO=%s, ONGOING=%s",
                _sec(float(TShared.SDP_REQ)),
                _sec(float(TShared.SUPPORTED_APP_PROTOCOL_REQ)),
                _sec(float(TShared.V2G_EVCC_COMMUNICATION_SETUP_TIMEOUT)),
                _sec(float(TShared.V2G_SECC_SEQUENCE_TIMEOUT)),
                _sec(float(TShared.V2G_EVCC_ONGOING_TIMEOUT)),
            )
        if T2 is not None:
            logger.info(
                "ISO15118-2: SESSION_SETUP=%s, SERVICE_DISCOVERY=%s, CPD=%s, CURRENT_DEMAND=%s",
                _sec(float(T2.SESSION_SETUP_REQ)),
                _sec(float(T2.SERVICE_DISCOVERY_REQ)),
                _sec(float(T2.CHARGE_PARAMETER_DISCOVERY_REQ)),
                _sec(float(T2.CURRENT_DEMAND_REQ)),
            )
        if T20 is not None:
            logger.info(
                "ISO15118-20: COMM_SETUP=%s, SEQ_TO=%s, AC_CL=%s, DC_CL=%s",
                _sec(float(T20.V2G_EVCC_COMMUNICATION_SETUP_TIMEOUT)),
                _sec(float(T20.V2G_SECC_SEQUENCE_TIMEOUT)),
                _sec(float(T20.V2G_SECC_SEQUENCE_TIMEOUT_AC_CL)),
                _sec(float(T20.V2G_SECC_SEQUENCE_TIMEOUT_DC_CL)),
            )
        if secc_config is not None:
            try:
                srv_to = getattr(secc_config, "server_start_timeout_s", None)
                logger.info("SECC server start timeout=%s", _sec(srv_to) if srv_to else "default")
            except Exception:
                pass
        # Report environment overrides for debugging
        try:
            import os

            caps = {
                "V2G_TIMEOUT_MIN_S": os.environ.get("V2G_TIMEOUT_MIN_S"),
                "V2G_TIMEOUT_MAX_S": os.environ.get("V2G_TIMEOUT_MAX_S"),
                "V2G_SECC_SEQUENCE_TIMEOUT_CAP_S": os.environ.get("V2G_SECC_SEQUENCE_TIMEOUT_CAP_S"),
                "V2G_EVCC_COMM_SETUP_TIMEOUT_CAP_S": os.environ.get("V2G_EVCC_COMM_SETUP_TIMEOUT_CAP_S"),
                "V2G_TIMEOUT_GRACE_S": os.environ.get("V2G_TIMEOUT_GRACE_S"),
                "V2G_TIMEOUT_GRACE_MAX": os.environ.get("V2G_TIMEOUT_GRACE_MAX"),
                "SLAC_WAIT_TIMEOUT_S": os.environ.get("SLAC_WAIT_TIMEOUT_S"),
                "SLAC_MAX_ATTEMPTS": os.environ.get("SLAC_MAX_ATTEMPTS"),
                "SLAC_RETRY_BACKOFF_S": os.environ.get("SLAC_RETRY_BACKOFF_S"),
                "SLAC_RESTART_HINT_MS": os.environ.get("SLAC_RESTART_HINT_MS"),
                "SLAC_RESTART_ON_DISCONNECT_MS": os.environ.get("SLAC_RESTART_ON_DISCONNECT_MS"),
            }
            # Only print those that are set/non-empty
            caps = {k: v for k, v in caps.items() if v}
            if caps:
                logger.info("Timer overrides: %s", caps)
        except Exception:
            pass
    except Exception:
        # Best effort; logging must not break startup
        pass
