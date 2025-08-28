Live Controller API — CCS DC Simulator + HAL (Pluggable)

This document describes the end‑to‑end HTTP API, execution model, and pluggable hardware abstraction (HAL) for the Live Controller used to orchestrate CCS DC charging in simulation or with real hardware drivers.

The controller exposes a FastAPI service for starting/stopping sessions, controlling contactor/CP/PWM, injecting faults, and reading meter/status. It can run stand‑alone or as the EVSE controller for the ISO 15118 SECC stack.

- API service entry: `src/ccs_sim/fastapi_app.py`
- Orchestrator: `src/ccs_sim/orchestrator.py`
- Precharge logic: `src/ccs_sim/precharge.py`
- HAL interfaces: `src/evse_hal/interfaces.py`
- HAL sim adapter: `src/evse_hal/adapters/sim.py`
- HAL registry: `src/evse_hal/registry.py`
- SECC HAL controller: `src/evse_hal/iso15118_hal_controller.py`
- EVSE launcher (SECC + PySLAC): `src/evse_main.py`

Overview

- Session lifecycle phases (observable via `/status.phase`): IDLE, HANDSHAKE, PRECHARGE, CHARGING, COMPLETE, ABORTED
- Fault handling: `/fault` aborts the session and records a `last_session_summary`.
- Contactor gating: output voltage/current drop to 0 when contactor is open.
- Metering: time‑weighted averages and energy (Wh) during a session; resets after completion/abort; copy of totals preserved in `last_session_summary`.
- Pluggable HAL: swap the sim drivers with hardware drivers by implementing the `EVSEHardware` interface and registering it.

Run Modes

- API server only (sim): `uvicorn src/ccs_sim/fastapi_app:app --reload`
- SECC + PySLAC + Controller:
  - Sim controller (default): `python src/evse_main.py --evse-id EVSE-1 --iface eth0`
  - HAL controller: `python src/evse_main.py --controller hal --evse-id EVSE-1 --iface eth0`
  - or `EVSE_CONTROLLER=hal python src/evse_main.py --evse-id EVSE-1 --iface eth0`

Environment for SECC

- `--cert-store` or `PKI_PATH`: path to ISO 15118 certificate store
- `--secc-config`: path to SECC `.env` file (see iso15118 docs)
- `--slac-config`: path to PySLAC `.env` file (if used directly)

REST API

Base URL: `http://127.0.0.1:8000`

Every endpoint returns HTTP 200 on success unless specified otherwise. Errors are returned as JSON with a `status` field and message.

Error format (example):

- `{ "status": "error", "message": "Session already in progress" }`

POST /start_session

Start a charging session. If a session is already active, returns `{ "status": "error" }`.

Request (JSON, all fields optional — defaults shown):
- `target_voltage` (float, default 400.0): DC target voltage (V)
- `initial_current` (float, default 50.0): Initial current request (A)
- `duration_s` (float, default 10.0): Charging duration after precharge (seconds)

Response:
- `{ "status": "started" }` or `{ "status": "error", "message": "Session already in progress" }`

GET /vehicle/bms

Expose EV (BMS) information learned during ISO 15118 handshake and charging. Values come from the SECC `EVDataContext` when running with SECC; in pure sim mode these fields may be null or default.

Response (JSON):
- `protocol` (string): `DIN_SPEC_70121|ISO_15118_2|ISO_15118_20` (if known)
- `evcc_id` (string|null): EVCC identifier
- `present_soc` (int|null): 0..100
- `present_voltage` (float|null): V
- `target_voltage` (float|null): V
- `target_current` (float|null): A
- `total_battery_capacity` (float|null): Wh (if provided)
- `energy_requests` (object):
  - `target_energy_request` (float|null)
  - `max_energy_request` (float|null)
  - `min_energy_request` (float|null)
- `soc_limits` (object):
  - `min_soc` (int|null)
  - `max_soc` (int|null)
  - `target_soc` (int|null)
- `rated_limits` (object):
  - `dc` (object): `max_charge_power`, `min_charge_power`, `max_charge_current`, `min_charge_current`, `max_voltage`, `min_voltage`, `max_discharge_power`, `min_discharge_power`, `max_discharge_current`
  - `ac` (object): `max_charge_current`, `min_charge_current`, `max_voltage`, `max_charge_power`, `min_charge_power`, `max_discharge_power` (subset shown)
- `session_limits` (object):
  - `dc` (object): `max_charge_power`, `min_charge_power`, `max_charge_current`, `min_charge_current`, `max_voltage`, `min_voltage`, `max_discharge_power`, `min_discharge_power`, `max_discharge_current`
  - `ac` (object): `max_charge_power`, `min_charge_power` (subset shown)

Notes:
- Mapped from `src/iso15118/iso15118/secc/controller/ev_data.py:EVDataContext`.
- During -2 CurrentDemand or -20 DCChargeLoop, `present_voltage`, `target_current`, `target_voltage` update over time.

POST /stop_session

Signal a session to stop. Transitions to `ABORTED` and records a summary.

Response: `{ "status": "stopping" }`

GET /vehicle/slac

Expose SLAC (HomePlug Green PHY) link information. Available when PySLAC runs as part of the SECC orchestration.

Response (JSON):
- `state` (string): `IDLE|MATCHING|MATCHED|FAILED`
- `ev_mac` (string|null): EV STA MAC address (e.g., `AA:BB:CC:DD:EE:FF`)
- `nid` (string|null): Network ID used for matching (hex)
- `run_id` (string|null): SLAC run identifier
- `attenuation_db` (number|null): Measured attenuation (if available)
- `last_updated` (string, ISO8601)

Notes:
- Derived from `pyslac.session.SlacEvseSession`. In sim‑only mode these values may be null.

GET /status

Return a snapshot of the controller state.

Response (JSON):
- `session_active` (bool)
- `phase` (string): `IDLE|HANDSHAKE|PRECHARGE|CHARGING|COMPLETE|ABORTED`
- `error` (nullable string)
- `contactor_closed` (bool)
- `cp_state` (string): `A|B|C|D|E`
- `voltage` (float)
- `current` (float)
- `energy_Wh` (float)
- `time_s` (float)
- `last_session_summary` (object|null):
  - `energy_Wh` (float)
  - `avg_voltage` (float)
  - `avg_current` (float)
  - `duration_s` (float)
  - `ended_phase` (string)
  - `error` (nullable string)
  - `session_params` (object):
    - `target_voltage` (float|null)
    - `initial_current` (float|null)
    - `duration_s` (float|null)
    - `requested_current` (float|null)

Example:

```
{
  "session_active": true,
  "phase": "CHARGING",
  "error": null,
  "contactor_closed": true,
  "cp_state": "C",
  "voltage": 400.0,
  "current": 30.0,
  "energy_Wh": 42.5,
  "time_s": 7.2,
  "last_session_summary": null,
  "session_params": {
    "target_voltage": 400.0,
    "initial_current": 50.0,
    "duration_s": 10.0,
    "requested_current": 30.0
  }
}
```

GET /vehicle/iso15118

Expose high‑level ISO 15118 session properties.

Response (JSON):
- `protocol` (string): Selected protocol
- `energy_service` (string|null): e.g., `DC_EXTENDED`, `AC_THREE_PHASE_CORE`
- `control_mode` (string|null): `SCHEDULED|DYNAMIC`
- `authorized` (bool|null): Result of authorization
- `evse_id` (string|null)
- `session_id` (string|null)
- `timestamps` (object): `started_at`, `last_message_at` (if tracked)

Notes:
- Values can be sourced from SECC session context once integrated into the API process.

HLC (ISO 15118) Control Endpoints

POST /hlc/start

Start the ISO 15118 SECC (HLC) in-process using the HAL controller.

Request (JSON):
- `iface` (string, default `"eth0"`): network interface to bind
- `secc_config` (string|null): path to SECC `.env` file
- `cert_store` (string|null): PKI store path (also accepts env `PKI_PATH`)

Response (JSON):
- `status` (object): same as `GET /hlc/status`

POST /hlc/stop

Stop the in-process SECC task.

Response (JSON):
- `status` (object): same as `GET /hlc/status`

GET /hlc/status

HLC manager status.

Response (JSON):
- `state` (string): `stopped|starting|running|error`
- `error` (string|null)
- `protocol_state` (string|null): last reported state from SECC
- `iface` (string|null)
- `session_id` (string|null)

Notes: Meter counters reset when the session ends; use `last_session_summary` for totals.

GET /meter

Return live metering only.

Response (JSON):
- `energy_Wh` (float)
- `avg_voltage` (float)
- `avg_current` (float)
- `session_time_s` (float)

POST /control/contactor

Open/close the contactor (relay).

Request (JSON): `closed` (bool)

Response: `{ "status": "ok", "contactor_closed": true|false }`

Effect: Opening the contactor forces `voltage=0` and `current=0` in `/status` and `/meter`.

POST /control/pwm

Set CP PWM duty cycle.

Request (JSON): `duty` (float, 0..100)

Response: `{ "status": "ok", "duty": <float> }`

POST /control/cp_state

Set simulated CP state (A..E). Hardware adapters should ignore this and derive CP from measurements.

Request (JSON): `state` (A|B|C|D|E)

Response: `{ "status": "ok", "state": "C" }`

POST /fault

Inject a fault; aborts the session and transitions to `ABORTED`.

Request (JSON): `type` (string: e.g., E_STOP, OVERCURRENT)

Response: `{ "status": "fault_injected", "type": "E_STOP" }`

Session Flow and Semantics

1) `start_session`
- Phase `HANDSHAKE` for ~2 s (simulated SLAC/ISO15118 setup window)
- CP moves to `C` (ready for DC) internally
2) Cable Check
- ~1 s wait; any `stop` or `fault` aborts (`ABORTED`)
3) `PRECHARGE`
- Ramp to `target_voltage` with ≤2 A limit; abort/timeout honored
4) `CHARGING`
- Close contactor, supply current; step down after 5 s (sim)
- Open contactor anytime to force no output
5) Completion
- After `duration_s`, set `COMPLETE`, capture `last_session_summary`, reset meter

Timeouts and parameters

- Handshake hold: ~2 s (simulated)
- Cable check hold: ~1 s
- Precharge: dynamic step size to reach `target_voltage` within timeout (default 10 s).
  - Implementation uses a step derived from `target_voltage` and timeout; see `src/ccs_sim/precharge.py`.
- Charging: current decreases after 5 s from `initial_current` to 30 A (simulator behavior).
- Stop/fault can abort from any phase. Abort sets `ABORTED` and records `last_session_summary`.

Edge cases handled

- Start while active: rejected with error JSON.
- Stop during HANDSHAKE/CABLE CHECK/PRECHARGE: safe abort.
- Fault injection during any phase: safe abort with `error` set to the injected fault.
- Contactor opened during CHARGING: output voltage/current reported as 0; session continues to count down.
- Meter resets at end; totals preserved in `last_session_summary`.

HAL (Pluggable Hardware Abstraction)

Interfaces (summarized):
- PWMController: `set_duty(%)`
- CPReader: `read_voltage()`, `simulate_state(A..E)`, `get_state()`
- ContactorDriver: `set_closed(bool)`, `is_closed()`
- DCPowerSupply: `set_voltage(V)`, `set_current_limit(A)`, `get_status()->(V,A)`
- Meter: `update(V,A)`, `get_energy_Wh()`, `get_avg_voltage()`, `get_avg_current()`, `get_session_time_s()`, `reset()`

Reference adapter: `src/evse_hal/adapters/sim.py`
Register new adapter in: `src/evse_hal/registry.py`

Implementing a new adapter

1. Create `src/evse_hal/adapters/<name>.py` implementing:
   - `EVSEHardware.pwm() -> PWMController`
   - `EVSEHardware.cp() -> CPReader`
   - `EVSEHardware.contactor() -> ContactorDriver`
   - `EVSEHardware.supply() -> DCPowerSupply`
   - `EVSEHardware.meter() -> Meter`
2. Register it in `src/evse_hal/registry.py` under a key (e.g., `"rpi"`).
3. Use via the orchestrator or SECC HAL controller by selecting the adapter.

Notes for RPi hardware

- PWM: prefer hardware PWM (e.g., `pigpio` or `RPi.GPIO` PWM) on a suitable pin.
- CP voltage: read via ADC (e.g., MCP3008 over `spidev`) with a proper divider.
- Contactor: drive with a protected transistor circuit; read contact feedback if available.
- Meter: integrate with a DC meter IC or simulate using supply telemetry.

SECC Integration (ISO 15118)

- Sim SECC controller: `python src/evse_main.py --evse-id EVSE-1 --iface eth0`
- HAL‑backed SECC controller: `python src/evse_main.py --controller hal --evse-id EVSE-1 --iface eth0`

SECC receives contactor and meter data via `HalEVSEController`. Certificates via `--cert-store` / `PKI_PATH`. SECC and SLAC `.env` files loaded with `--secc-config` and `--slac-config`.

HalEVSEController overrides (mapping to HAL)

- `is_contactor_closed()` / `is_contactor_opened()` → `hal.contactor()`
- `get_meter_info_v2()` / `get_meter_info_v20()` → `hal.meter()`
- `get_cp_state()` → approximate mapping from `hal.cp().get_state()`
- All other SECC behaviors default to `SimEVSEController` unless extended.

Surfacing EV/SLAC data to the API

- `/vehicle/bms`: maps to `EVDataContext` via the in-process HLC manager when HLC is running; otherwise falls back to orchestrator snapshot.
- `/vehicle/slac`: maps to `pyslac.session.SlacEvseSession` once integrated.
- `/vehicle/iso15118`: maps to SECC session (protocol, energy service, control mode, auth, IDs). Currently exposes `protocol_state` and `session_id` placeholders via the HLC manager.

Error Handling and Status Codes

- `POST /start_session` returns `{ "status": "error", "message": "Session already in progress" }` if active
- Control endpoints validate inputs (`duty` 0..100, CP state pattern `^[ABCDE]$`)
- All endpoints return HTTP 200. Secure the API in production (no auth included).

Validation

- `/control/pwm`: `duty` must be 0..100 (float accepted).
- `/control/cp_state`: one of `A,B,C, D, E`.
- `/start_session`: `target_voltage`/`initial_current`/`duration_s` must be non‑negative; `duration_s` ≥ 1.0.

Concurrency and idempotency

- Single active session is enforced. Repeated `/start_session` while active returns error.
- Control endpoints affect the single active session; they are not idempotent but safe to repeat.

Testing

- Run `pytest tests -q` to execute API tests: 5 tests cover start/stop, status progression, contactor behavior, fault abort, and endpoint availability.

Examples

Start session:
- `curl -s -X POST localhost:8000/start_session -H 'content-type: application/json' -d '{"target_voltage":400,"initial_current":50,"duration_s":10}'`

Poll status:
- `watch -n1 curl -s localhost:8000/status | jq`

Open/close contactor:
- `curl -s -X POST localhost:8000/control/contactor -H 'content-type: application/json' -d '{"closed":false}'`
- `curl -s -X POST localhost:8000/control/contactor -H 'content-type: application/json' -d '{"closed":true}'`

Inject fault:
- `curl -s -X POST localhost:8000/fault -H 'content-type: application/json' -d '{"type":"E_STOP"}'`

Read meter:
- `curl -s localhost:8000/meter | jq`

Notes & Limitations

- Simulation models CCS DC flow with simplified control logic; ISO 15118 protocol is handled by the library, not the API.
- Meter counters reset after session end; use `last_session_summary` for totals.
- The API has no authentication; keep it on a trusted network or add auth.
