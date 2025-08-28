from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Optional
import threading
try:
    from . import pwm
    from .orchestrator import ChargeOrchestrator
except ImportError:  # executed from within package root
    import pwm
    from orchestrator import ChargeOrchestrator

app = FastAPI()
orch = ChargeOrchestrator()

class StartSessionRequest(BaseModel):
    target_voltage: float = Field(400.0, ge=0, description="Target DC voltage (V)")
    initial_current: float = Field(50.0, ge=0, description="Initial current request (A)")
    duration_s: float = Field(10.0, ge=1.0, description="Charging duration (s)")


@app.post("/start_session")
def start_session(body: StartSessionRequest = StartSessionRequest()):
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
    orch.stop_session()
    return {"status": "stopping"}

@app.get("/status")
def status():
    return orch.snapshot()


class ContactorRequest(BaseModel):
    closed: bool


@app.post("/control/contactor")
def control_contactor(body: ContactorRequest):
    orch.set_contactor(body.closed)
    return {"status": "ok", "contactor_closed": body.closed}


class PWMRequest(BaseModel):
    duty: float = Field(..., ge=0.0, le=100.0)


@app.post("/control/pwm")
def control_pwm(body: PWMRequest):
    orch.set_pwm_duty(body.duty)
    return {"status": "ok", "duty": body.duty}


class CPStateRequest(BaseModel):
    state: str = Field(..., regex=r"^[ABCDE]$")


@app.post("/control/cp_state")
def control_cp_state(body: CPStateRequest):
    orch.set_cp_state(body.state)
    return {"status": "ok", "state": body.state}


@app.get("/meter")
def meter():
    m = orch.hal.meter()
    return {
        "energy_Wh": m.get_energy_Wh(),
        "avg_voltage": m.get_avg_voltage(),
        "avg_current": m.get_avg_current(),
        "session_time_s": round(m.get_session_time_s(), 2),
    }


class FaultRequest(BaseModel):
    type: str = Field(..., description="Fault type identifier, e.g., E_STOP, OVERCURRENT")


@app.post("/fault")
def inject_fault(body: FaultRequest):
    orch.inject_fault(body.type)
    return {"status": "fault_injected", "type": body.type}
