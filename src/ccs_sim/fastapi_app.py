from fastapi import FastAPI
import threading
try:
    from . import pwm
    from .orchestrator import ChargeOrchestrator
except ImportError:  # executed from within package root
    import pwm
    from orchestrator import ChargeOrchestrator

app = FastAPI()
orch = ChargeOrchestrator()

@app.post("/start_session")
def start_session():
    if orch.session_active:
        return {"status": "error", "message": "Session already in progress"}
    threading.Thread(target=orch.run_session).start()
    return {"status": "started"}

@app.get("/status")
def status():
    volts, amps = orch.supply.get_status()
    return {
        "session_active": orch.session_active,
        "cp_state": pwm._simulated_cp_state,
        "voltage": volts,
        "current": amps,
        "energy_Wh": orch.meter.get_total_energy_wh(),
        "time_s": round(orch.meter.get_session_time(), 1),
    }
