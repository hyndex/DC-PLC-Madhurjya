import logging
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from typing import Optional
import threading
import asyncio
from src.hlc.manager import hlc
from src.hlc.slac import slac as slac_mgr
try:
    from . import pwm
    from .orchestrator import ChargeOrchestrator
except ImportError:  # executed from within package root
    import pwm
    from orchestrator import ChargeOrchestrator

logger = logging.getLogger("api")
app = FastAPI()

# Configure logging if not already configured (use shared util)
try:
    from src.util.logging import setup_logging as _setup_logging
except Exception:
    try:
        from util.logging import setup_logging as _setup_logging
    except Exception:
        _setup_logging = None
if _setup_logging:
    _setup_logging()
orch = ChargeOrchestrator()

class StartSessionRequest(BaseModel):
    target_voltage: float = Field(400.0, ge=0, description="Target DC voltage (V)")
    initial_current: float = Field(50.0, ge=0, description="Initial current request (A)")
    duration_s: float = Field(10.0, ge=1.0, description="Charging duration (s)")


@app.post("/start_session")
def start_session(body: StartSessionRequest = StartSessionRequest()):
    logger.info("POST /start_session", extra=body.dict())
    if orch.session_active:
        return {"status": "error", "message": "Session already in progress"}
    started = orch.start_session(
        target_voltage=body.target_voltage,
        initial_current=body.initial_current,
        duration_s=body.duration_s,
    )
    return {"status": "started" if started else "error"}


@app.post("/stop_session")
def stop_session():
    logger.info("POST /stop_session")
    orch.stop_session()
    return {"status": "stopping"}

@app.get("/status")
def status():
    logger.debug("GET /status")
    return orch.snapshot()


class ContactorRequest(BaseModel):
    closed: bool


@app.post("/control/contactor")
def control_contactor(body: ContactorRequest):
    logger.info("POST /control/contactor", extra=body.dict())
    orch.set_contactor(body.closed)
    return {"status": "ok", "contactor_closed": body.closed}


class PWMRequest(BaseModel):
    duty: float = Field(..., ge=0.0, le=100.0)


@app.post("/control/pwm")
def control_pwm(body: PWMRequest):
    logger.info("POST /control/pwm", extra=body.dict())
    try:
        orch.set_pwm_duty(body.duty)
        return {"status": "ok", "duty": body.duty}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class CPStateRequest(BaseModel):
    state: str = Field(..., regex=r"^[ABCDE]$")


@app.post("/control/cp_state")
def control_cp_state(body: CPStateRequest):
    logger.info("POST /control/cp_state", extra=body.dict())
    try:
        orch.set_cp_state(body.state)
        return {"status": "ok", "state": body.state}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/cp")
def cp_status():
    logger.debug("GET /cp")
    cp = orch.hal.cp()
    try:
        v = float(cp.read_voltage())
    except Exception:
        v = None
    try:
        st = cp.get_state()
    except Exception:
        st = None
    return {"voltage_v": v, "state": st}


@app.get("/meter")
def meter():
    m = orch.hal.meter()
    return {
        "energy_Wh": m.get_energy_Wh(),
        "avg_voltage": m.get_avg_voltage(),
        "avg_current": m.get_avg_current(),
        "session_time_s": round(m.get_session_time_s(), 2),
    }


@app.get("/vehicle/bms")
def vehicle_bms():
    logger.debug("GET /vehicle/bms")
    # Prefer HLC EV data if available, else fall back to orchestrator snapshot
    hlc_bms = hlc.bms_snapshot()
    if hlc_bms:
        return {"protocol": None, **hlc_bms}
    snap = orch.snapshot()
    volts = snap.get("voltage")
    sp = (snap.get("session_params") or {})
    return {
        "protocol": None,
        "evcc_id": None,
        "present_soc": None,
        "present_voltage": volts,
        "target_voltage": sp.get("target_voltage"),
        "target_current": sp.get("requested_current"),
        "total_battery_capacity": None,
        "energy_requests": {"target_energy_request": None, "max_energy_request": None, "min_energy_request": None},
        "soc_limits": {"min_soc": None, "max_soc": None, "target_soc": None},
        "rated_limits": {"dc": {}, "ac": {}},
        "session_limits": {"dc": {}, "ac": {}},
    }


@app.get("/vehicle/slac")
def vehicle_slac():
    logger.debug("GET /vehicle/slac")
    return slac_mgr.status()


@app.get("/vehicle/iso15118")
def vehicle_iso15118():
    logger.debug("GET /vehicle/iso15118")
    st = hlc.status()
    return {
        "protocol": st.get("protocol_state"),
        "energy_service": None,
        "control_mode": None,
        "authorized": None,
        "evse_id": None,
        "session_id": st.get("session_id"),
        "timestamps": {"started_at": None, "last_message_at": None},
    }


@app.get("/vehicle/live")
def vehicle_live():
    """Aggregate live view for quick debugging: CP, SLAC, ISO and BMS."""
    cp = orch.hal.cp()
    try:
        v = float(cp.read_voltage())
    except Exception:
        v = None
    try:
        st_cp = cp.get_state()
    except Exception:
        st_cp = None
    hlc_bms = hlc.bms_snapshot() or {}
    return {
        "cp": {"voltage_v": v, "state": st_cp},
        "slac": slac_mgr.status(),
        "iso15118": {"state": (hlc.status() or {}).get("protocol_state")},
        "bms": hlc_bms,
    }


class HLCStartRequest(BaseModel):
    iface: str = Field("eth0")
    secc_config: Optional[str] = None
    cert_store: Optional[str] = None


@app.post("/hlc/start")
async def hlc_start(body: HLCStartRequest = HLCStartRequest()):
    logger.info("POST /hlc/start", extra=body.dict())
    await hlc.start(body.iface, body.secc_config, body.cert_store)
    return {"status": hlc.status()}


@app.post("/hlc/stop")
async def hlc_stop():
    logger.info("POST /hlc/stop")
    await hlc.stop()
    return {"status": hlc.status()}


@app.get("/hlc/status")
def hlc_status():
    return hlc.status()


class SlacMatchRequest(BaseModel):
    ev_mac: str
    nid: Optional[str] = None
    run_id: Optional[str] = None
    attenuation_db: Optional[float] = None
    iface: str = Field("eth0")
    secc_config: Optional[str] = None
    cert_store: Optional[str] = None


@app.post("/slac/start_matching")
def slac_start_matching():
    logger.info("POST /slac/start_matching")
    slac_mgr.start_matching()
    return slac_mgr.status()


@app.post("/slac/matched")
async def slac_matched(body: SlacMatchRequest):
    logger.info("POST /slac/matched", extra=body.dict())
    slac_mgr.matched(body.ev_mac, body.nid, body.run_id, body.attenuation_db)
    # Start HLC upon match
    await hlc.start(body.iface, body.secc_config, body.cert_store)
    return {"slac": slac_mgr.status(), "hlc": hlc.status()}


class FaultRequest(BaseModel):
    type: str = Field(..., description="Fault type identifier, e.g., E_STOP, OVERCURRENT")


@app.post("/fault")
def inject_fault(body: FaultRequest):
    logger.warning("POST /fault", extra=body.dict())
    orch.inject_fault(body.type)
    return {"status": "fault_injected", "type": body.type}
