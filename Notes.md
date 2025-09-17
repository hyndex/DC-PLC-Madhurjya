# EVSE HAL + ISO 15118 End-to-End Notes

This document captures, in order, everything done during the session to
bring up the HAL, SLAC, SECC, and HLC; the issues found; the fixes
applied; how to run in AC and DC modes; and how to capture EV details
(BMS/Precharge) from the logs.

---

## 1) Initial Repository Survey

- Identified orchestration entrypoint: `src/evse_main.py` (handles SLAC
  and launches SECC on match; supports HAL and sim backends).
- ISO15118 stack vendored under `src/iso15118`; PySLAC vendored under
  `src/pyslac`.
- HAL adapters under `src/evse_hal/adapters/`:
  - `esp_uart.py` (ESP32‑S3 CP helper firmware over UART)
  - `esp_periph_uart.py` (alt peripheral)
  - `sim.py` (simulated)
- ESP32‑S3 CP firmware source at
  `firmware/esp32s3_cp/src/main.cpp` (ADC sampling, CP state, JSON RPC
  over UART).
- Helper script to start in HAL mode:
  `scripts/start_evse_hal.sh` (detects interface/port; exports env; runs
  `src/evse_main.py` and tees JSON logs).

---

## 2) First Bring-up (HAL mode)

Command (after `chmod +x scripts/start_evse_hal.sh`):

- `PLC_IFACE=eth1 ESP_CP_PORT=/dev/ttyACM0 EVSE_TEE_JSON=/tmp/evse_run.jsonl`
- `scripts/start_evse_hal.sh --evse-id EVSE-1 --iface eth1 --port /dev/ttyACM0 --adapter esp-uart --json /tmp/evse_run.jsonl`

Observed:
- SLAC flow runs (CM_SET_KEY → MNBC sounds → ATTEN_CHAR → MATCH).
- EV MAC captured and logged (example):
  `fc:d6:bd:ff:cd:67`.
- SECC started and bound: `UDP server started at FF02::1%eth1:15118`.

Issue:
- Peer persistence stored Python bytes reprs (e.g., `b'\xfc...'`).

Fix:
- `src/evse_main.py` normalized SLAC peer persistence to colon-hex.
  - Function: `_log_slac_peer()` now writes colon-hex into
    `/tmp/evse_slac_peer.json`.

---

## 3) CP State Handling and “ESP-only” Requirement

Goal: Drive SLAC/SECC strictly from ESP firmware CP state, without any
Pi-side state simulation or debouncing.

Changes:
- `src/evse_hal/adapters/esp_uart.py`:
  - Removed Pi-side CP debouncing. Now `get_state()` returns the ESP’s
    `state` letter (A/B/C/D/E/F) exactly.
  - Added guarded logging in `read_voltage()`.
- `src/evse_main.py`:
  - Added env gate `EVSE_CP_HOST_HINTS` to control any host-driven CP
    hints (pulses). Default off unless explicitly enabled.
  - Wrapped all CP manipulation (esp_set_mode/pwm, restart SLAC hint)
    behind `EVSE_CP_HOST_HINTS`.

Result:
- CP transitions forwarded to PySLAC/SECC come directly from ESP
  firmware without Pi recomputation.

---

## 4) AC vs DC Mode Clarification + Configuration

Background:
- AC Type 2 uses IEC 61851 PWM to indicate available current. HLC for AC
  is distinct (and many EVs don’t share SoC via AC flows).
- DC (CCS Combo‑2) uses 5% duty in B/C/D to signal HLC present and
  negotiates DC PreCharge/CurrentDemand (with BMS targets).

Problems observed:
- Running with AC cable while in DC `5%` mode caused EV not to proceed.
- HLC didn’t start because EV never sent SDP.

Fixes:
- `src/evse_hal/adapters/esp_uart.py`:
  - New startup mode via env variables:
    - `ESP_CP_MODE` / `EVSE_CP_MODE`: `dc` (default) or `manual` (for
      AC PWM).
    - For AC/manual, set `EVSE_AC_MAX_CURRENT_A` (e.g., `16` amps). Duty
      ≈ A/0.6, clamped [10..85].
  - New `ac_hlc_nudge(reset_ms=350)`: brief 5% pulse then restore AC
    duty (only used when `EVSE_CP_HOST_HINTS=1`).
- `src/evse_main.py`:
  - Nudge path chooses AC or DC hint based on configured CP mode.
- `secc.env`:
  - Ensured DIN/ISO enabled and we honor EV priority
    (`SECC_SAP_PREFER_EV_PRIORITY=1`).

How to run AC:
- `ESP_CP_MODE=manual EVSE_AC_MAX_CURRENT_A=16 EVSE_CP_HOST_HINTS=1`
- Start via `scripts/start_evse_hal.sh` as above.

How to run DC (for BMS/Precharge):
- `ESP_CP_MODE=dc EVSE_CP_HOST_HINTS=1`
- Use a CCS DC (Combo‑2) cable.

---

## 5) IPv6, Interface, and Logs

- Verified `eth1` is up and has IPv6 link-local; SECC binds to
  `FF02::1%eth1:15118`.
- JSON tee path: `/tmp/evse_run.jsonl`.
- Created watchers:
  - `scripts/wait_bms.py`: tails JSON log and writes first HLC BMS
    snapshot to `/tmp/evse_bms_snapshot.json`.
  - `scripts/wait_hlc_phases.py`: records HLC phase milestones
    (SessionSetup, ServiceDiscovery, Authorization, CPD) to
    `/tmp/hlc_progress.json`.

---

## 6) UART Flapping Root Cause and Fix

Symptom:
- After SECC starts, repeated `esp.cp: ESP CP serial reconnected` every
  few seconds; occasional timeouts.

Root cause:
- Two HAL instances were created:
  1) One inside `EVSECommunicationController.start()` for CP/SLAC.
  2) One inside SECC startup (`start_secc` / `launch_secc_background`).
- Both opened `/dev/ttyACM0`, racing on the same UART.

Fix in `src/evse_main.py`:
- Reuse the same `hal` instance for SECC:
  - `_start_secc_bg()` now calls
    `launch_secc_background(..., existing_hal=hal)`.
  - `start_secc()` and `launch_secc_background()` accept `existing_hal`
    and pass it to `HalEVSEController` instead of calling
    `create_hal(adapter)` again.

Result:
- Only one HAL uses the UART. Serial reconnect spam stops.
- SLAC still matches; SECC remains READY; environment stable.

Optional system hardening (if still flapping on some hosts):
- Disable ModemManager: `sudo systemctl disable --now ModemManager`.
- Disable any `serial-getty` on `/dev/ttyACM0` (usually not present by
  default): `sudo systemctl disable --now serial-getty@ttyACM0`.
- Udev rule to set `ENV{ID_MM_DEVICE_IGNORE}="1"` for ESP VID/PID.

---

## 7) HLC Status and Evidence

Observed in logs (examples):
- SLAC: MATCHED, EV MAC `fc:d6:bd:ff:cd:67`, NID/Run ID logged.
- SECC: `Communication session handler started`; `UDP server started at
  address FF02::1%eth1 and port 15118`; `ServiceStatus.READY`.
- For AC attempts: EV did not send SDP; no `name="hlc"` or phase logs.
- For DC attempts (with `ESP_CP_MODE=dc`): SLAC matched; SECC READY;
  still no SDP/HLC visible; in one run, the ISO socket showed
  `IncompleteReadError(0 of 8 bytes)`, meaning the EV immediately
  closed a newly opened session—typical of an EV not fully in DC-ready
  state or aborting due to policy/config.

Conclusion:
- Pipeline up to SECC READY is good.
- HLC remains pending on EV behavior. Ensure DC cable (Combo‑2) is used
  and EV is in DC charging mode; confirm any OEM “start charging” UI.

---

## 8) Files Added / Modified (Summary)

- Added:
  - `secc.env` (protocol order + robustness settings; EV priority honored).
  - `scripts/wait_bms.py` (first HLC snapshot capture).
  - `scripts/wait_hlc_phases.py` (milestone tracker for HLC phases).
- Modified:
  - `src/evse_main.py`:
    - Normalize SLAC EV MAC persistence (colon-hex).
    - Gate all CP hints behind `EVSE_CP_HOST_HINTS`.
    - Add AC/DC-aware nudge selection.
    - Reuse single HAL instance for SECC to avoid double-opening UART.
  - `src/evse_hal/adapters/esp_uart.py`:
    - Remove Pi-side CP debouncing; trust ESP CP state.
    - Add startup mode selection via `ESP_CP_MODE`/`EVSE_CP_MODE`.
    - For AC, compute PWM duty from `EVSE_AC_MAX_CURRENT_A`.
    - Add `ac_hlc_nudge()`.
  - `scripts/start_evse_hal.sh`:
    - Export `EVSE_CP_HOST_HINTS` default; pass through to child env.

---

## 9) How to Run (Cheat Sheet)

DC (to get BMS/Precharge):
- Hardware: CCS Combo‑2 (DC) cable; EV must be DC-capable and in
  DC‑ready state.
- Env:
  - `ESP_CP_MODE=dc EVSE_CP_HOST_HINTS=1`
  - `PLC_IFACE=eth1 ESP_CP_PORT=/dev/ttyACM0 SECC_CONFIG_PATH=$(pwd)/secc.env`
- Start:
  - `scripts/start_evse_hal.sh --evse-id EVSE-1 --iface eth1 --port /dev/ttyACM0 --adapter esp-uart --json /tmp/evse_run.jsonl`
- Capture:
  - `tail -f /tmp/evse_run.jsonl` (look for `name="hlc"` entries)
  - `/tmp/evse_bms_snapshot.json` and `/tmp/hlc_progress.json`

AC (no DC BMS; AC HLC may not expose SoC):
- Hardware: AC Type 2 cable.
- Env:
  - `ESP_CP_MODE=manual EVSE_AC_MAX_CURRENT_A=16 EVSE_CP_HOST_HINTS=1`
  - same `PLC_IFACE`, `ESP_CP_PORT`, `SECC_CONFIG_PATH` as above
- Start and watch logs as above.

---

## 10) Open Items / Next Steps

- Still waiting for EV to initiate SDP/HLC in DC mode. When the EV is in
  proper DC charging state, HLC should proceed. We’ll capture and report:
  - `iso_state` progression (SessionSetup → ServiceDiscovery →
    Authorization → CPD → PreCharge/CurrentDemand), and
  - `bms` fields (e.g., `present_soc`, `target_voltage`, `target_current`).
- If the EV prefers a specific protocol order (DIN first or ISO 15118-2
  only), we can tune `secc.env` `PROTOCOLS` accordingly.

---

## 11) Useful One-Liners

- EV MAC/NID/RUN_ID:
  - `python -c "from src.util.slac_peer_store import read_peer; print(read_peer())"`
- HLC last snapshot (if present):
  - python - << 'PY'
import json, pathlib
for line in reversed(pathlib.Path('/tmp/evse_run.jsonl').read_text().splitlines()):
    try: obj=json.loads(line)
    except: continue
    if obj.get('name')=='hlc' and 'bms' in obj:
        print({'iso_state':obj.get('iso_state'),'bms':obj['bms'],'evse':obj.get('evse')}); break
else:
    print('No HLC/BMS entries yet')
PY
- HLC phase tracker output:
  - `cat /tmp/hlc_progress.json`
- First HLC snapshot file:
  - `cat /tmp/evse_bms_snapshot.json`

---

## 12) Environment Variables (Key)

- `ESP_CP_MODE` / `EVSE_CP_MODE`: `dc` or `manual` (AC PWM)
- `EVSE_AC_MAX_CURRENT_A`: advertised AC current (Amps)
- `EVSE_CP_HOST_HINTS`: `1` to allow host nudges (AC/DC pulses); `0` to disable
- `EVSE_HAL_ADAPTER`: `esp-uart` (or `esp-periph`)
- `ESP_CP_PORT`: e.g., `/dev/ttyACM0`
- `PLC_IFACE`: network interface for SLAC/SECC, e.g., `eth1`
- `SECC_CONFIG_PATH`: path to `secc.env`
- `EVSE_TEE_JSON`: tee JSON path (e.g., `/tmp/evse_run.jsonl`)

---

## 13) Final Status

- UART contention fixed (single HAL/serial user).
- SLAC: working and matches the EV, EV MAC captured.
- SECC: up and listening on `eth1`.
- HLC: pending EV initiation (no SDP yet in current logs). Watchers are
  running to capture milestones and BMS data as soon as the EV begins
  HLC.

*** End of Notes ***
***

## 14) AC Test Push – Updates (SAP → SessionSetup observed)

Run context
- Mode: AC (manual PWM) with 16 A advertised (duty ≈ 27%).
- HAL adapter: `esp-uart` on `/dev/ttyACM0`.
- Interface: `eth1` with IPv6 link‑local.
- Start: `scripts/start_evse_hal.sh --evse-id EVSE-AC --iface eth1 --port /dev/ttyACM0 --adapter esp-uart --json /tmp/evse_run.jsonl`
- AC PWM helper used: `python scripts/esp_ac_pwm.py --port /dev/ttyACM0 --set-ac-amps 16`.

Observed (JSON log `/tmp/evse_run.jsonl`)
- SDP received; SECC bound TCP and accepted EV connection.
- “Entered state SupportedAppProtocol”.
- Decoded SAP request: EV offered `DIN 70121 v2.0` and `ISO 15118‑2 v2.0`.
- Chosen protocol: `ISO_15118_2`; responded `OK_SuccessfulNegotiation`.
- “Entered state SessionSetup”.
- No `ServiceDiscovery/Authorization/ChargeParameterDiscovery (AC)` observed yet (EV must proceed/authorize).

Fix for AC/DC capability alignment
- `src/evse_hal/iso15118_hal_controller.py:get_supported_energy_transfer_modes()` now advertises AC‑only when HAL CP mode is AC/manual, DC‑only when in DC.
  This removes the mismatch where EVSE could appear DC‑capable while signaling AC PWM.

AC control from Pi
- HAL: `set_ac_current(amps)` auto‑sets manual mode + duty (IEC 61851 mapping), and `cp_mode()` reads current mode.
- CLI: `scripts/esp_ac_pwm.py` to set mode/duty or amps directly.

Next actions
- Keep the car in “AC charging ready/authorized” state; some EVs don’t proceed past SessionSetup until user confirms AC charging.
- Optionally set ISO‑only in `secc.env` (`PROTOCOLS=ISO_15118_2,ISO_15118_20`) to simplify protocol selection.
- Watchers:
  - `/tmp/hlc_progress.json` – phases tracked by `scripts/wait_hlc_phases.py`.
  - `/tmp/evse_run.jsonl` – search for `Entered state ServiceDiscovery|Authorization|ChargeParameterDiscovery|ChargeLoop`.

Artifacts
- AC PWM set: “HAL ESP set_mode(manual), duty_percent=27, ac_max_a=16.0”.
- SAP and SessionSetup entries recorded with timestamps in the JSON log.


Here’s what I’ve done and what we have now.

Notes.md updated: Added Section 14 summarizing the AC push, including:
AC PWM set to 16 A (27% duty), HAL/SECC startup, SAP negotiation, SessionSetup entry.
New fix that aligns advertised energy transfer modes (AC-only vs DC-only) to the HAL CP mode.
How to proceed further and what to look for in logs.
secc.env updated: Set PROTOCOLS=ISO_15118_2,ISO_15118_20 to simplify AC negotiation (ISO-only).
Relaunched AC HAL + SECC and re-validated:
HAL set to manual (AC) mode, PWM configured.
SLAC started and runs SetKey through to sounds; it reliably matches in repeated runs (your EV MAC remains fc:d6:bd:ff:cd:67).
ISO: SDP → SupportedAppProtocol (EV offered DIN + ISO) → SECC chose ISO_15118_2 → Entered SessionSetup.
Current AC ISO status

SupportedAppProtocol: Completed (EV offered DIN + ISO; SECC negotiated ISO_15118_2).
SessionSetup: Entered (seen in log).
ServiceDiscovery/Authorization/AC_CPDiscovery: Not yet observed in the log tails. Many EVs require explicit AC charge authorization on the vehicle UI before sending the next messages.
Key changes made to remove logical blockers

AC/DC capability alignment: The SECC now advertises AC-only modes when HAL is in AC/manual mode, and DC-only when in DC. This prevents EV confusion (previously, we could appear DC-capable while signaling AC PWM).
HAL reuse into SECC: Avoids double-opening the ESP UART; serial reconnects stopped.
ESP-only CP state: No Pi-side debounce or state simulation; the ESP’s CP states drive SLAC/SECC.
Host AC PWM control: Added set_ac_current(amps) to HAL and scripts/esp_ac_pwm.py for manual tests.
What to do next to push AC further

On the EV: Ensure “AC charging ready/authorized” is active; some EVs won’t proceed past SessionSetup until the user authorizes AC charging.
Keep watching /tmp/evse_run.jsonl for:
“Entered state ServiceDiscovery”
“Entered state Authorization”
“Entered state ChargeParameterDiscovery” or “AC_ChargeParameterDiscovery”
Optional: Increase the dwell time with the session running; the EV may take a short delay before sending the next state.
Quick commands

HLC phases:
egrep -n "Entered state (SupportedAppProtocol|SessionSetup|ServiceDiscovery|Authorization|ChargeParameterDiscovery|ChargeLoop)" /tmp/evse_run.jsonl
Live JSON tail:
tail -f /tmp/evse_run.jsonl
Notes:
cat Notes.md
If you want, I can keep the AC session running and post the first “ServiceDiscovery/Authorization/AC CPD” phase timestamps here the moment they appear. If your goal shifts to SoC specifically, we should switch to DC (Combo‑2) since SoC is typically provided in DC CurrentDemand flows.

---

## 15) DC Field Bring‑Up with Real Vehicle (Try6/Try7)

Run context
- Hardware: RPi + QCA7000 (qcaspi) on `spi0.0`, ESP32‑S3 CP over `/dev/ttyACM0`, CCS2 vehicle connected.
- PLC driver: `qcaspi 0.2.7-i` with overlay `dtoverlay=qca7000,int_pin=25,speed=12000000`.
- Detected PLC iface: `eth1` (driver=qcaspi).
- HAL adapter: `esp-uart`.
- Start command (Try6 style):
  ```bash
  export SECC_CONFIG_PATH=$PWD/secc.env
  export SLAC_CONFIG_PATH=$PWD/slac.env
  export PLC_IFACE=eth1
  export ESP_CP_PORT=/dev/ttyACM0
  export EVSE_CP_HOST_HINTS=1
  export EVSE_PLC_SOFT_RESET=0  # use 1 if needed
  export EVSE_ID=INJPSE0006360
  . .venv/bin/activate
  timeout 180s scripts/start_evse_hal.sh --evse-id "$EVSE_ID" --iface "$PLC_IFACE" --port "$ESP_CP_PORT" --adapter esp-uart > /tmp/evse_e2e.log 2>&1
  ```

Observed (high‑level)
- SLAC: CM_SET_KEY succeeded; SLAC matched.
  - EV MAC observed: `38:1f:26:33:9d:b0`
  - Run‑ID: `f5:ea:e3:7f:b4:6f:ae:62`
- ISO 15118 (TCP on fe80::…%eth1:<port>):
  - SupportedAppProtocol → SessionSetup → ServiceDiscovery → Authorization → ChargeParameterDiscovery → CableCheck → PreCharge
  - PowerDelivery(Start) accepted
  - Entered CurrentDemand and replied once, then EV closed TCP (IncompleteReadError on our side).

Representative log tail (from `/tmp/evse_e2e.log`)
- `SLAC MATCHED Successfully, Link Established`
- `UDP server started at address FF02::1%eth1 and port 15118`
- `TCP server started at address fe80::…%eth1 and port 50119`
- `Entered state ...` through PreCharge, then:
  - `Entered state CurrentDemand`
  - `CurrentDemandReq received`
  - `Sent CurrentDemandRes`
  - `IncompleteReadError: 0 bytes read on a total of 8 expected bytes` (peer closed)

What this means
- PLC and HLC succeeded; contactor/AUX passed CableCheck.
- The EV initiated power transfer, but terminated the HLC quickly after the first CurrentDemand exchange. Typical causes:
  - EV didn’t see expected power stage behavior (voltage/current not tracking its request).
  - CP transitioned briefly (mechanical latch, connector movement).
  - EV‑specific timing/quirk (duplicate retries are visible; duplicates were handled).

Actionable next steps
1) Capture structured HLC/BMS snapshot for diagnosis
   - Run with tee: `EVSE_TEE_JSON=/tmp/evse_e2e.jsonl` and re‑run start.
   - Watchers:
     - `python scripts/wait_hlc_phases.py --log /tmp/evse_e2e.jsonl --timeout 0`
     - `python scripts/wait_bms.py --log /tmp/evse_e2e.jsonl --timeout 0`
   - Inspect first BMS snapshot (`/tmp/evse_bms_snapshot.json`): target_voltage/current, present_voltage, SOC.
2) Verify CP stability during CurrentDemand
   - `ESP_CP_PORT=/dev/ttyACM0 ./scripts/cp_monitor.py --duration 30`
3) If bench‑testing without real DC stage
   - Temporarily simulate supply to follow setpoints: `EVSE_SIM_SUPPLY=1`.
   - Confirm HLC continues through CurrentDemand loop.
4) Reduce latency noise
   - Keep CPU governor at performance; avoid heavy logging; JSON tee is fine.

QCA7000/qcaspi reliability notes
- We improved `scripts/plc_soft_reset.sh` to default `qcaspi_pluggable=1` and auto‑detect the qcaspi netdev (no hardcoded `eth1`).
- If SLAC init stalls, try conservative SPI params before starting: `QCASPI_CLKSPEED=8000000 QCASPI_BURST=3000` (or 4 MHz / 1200 for worst cases).

Productionization checklist (DC CCS2)
- System
  - systemd services for HAL/SECC (order after serial and qcaspi ready).
  - Persistent module options: `/etc/modprobe.d/qcaspi.conf` with `qcaspi_pluggable=1 qcaspi_clkspeed=8000000 qcaspi_burst_len=3000`.
  - udev rules to name ESP serial predictably and set permissions.
  - Ensure IPv6 link‑local on PLC iface (script already enforces; verify netplan doesn’t disable IPv6).
  - CPU governor=performance; log rotation for `/tmp/evse_*.jsonl` or move to `/var/log/evse/`.
- Certificates / IDs
  - Valid EVSEID in `secc.env`; manage PKI under `pki/`.
- Hardware integration
  - Contactor coil + AUX prove‑out; polarity and timing aligned.
  - DC supply control and metering plumbed (move from `sim` to real via `esp-periph` or dedicated driver). Verify ramp and current limit.
  - Thermal monitoring and derating thresholds per site.

Artifacts and helpers added in this work
- `scripts/run_until_bms.sh`: now accepts `--iface/--port/--adapter`, loops until first BMS snapshot.
- `scripts/start_secc_only.py`: start SECC without SLAC for bench tests.
- `scripts/send_sdp.py`: send SDP over IPv6/UDP and print SECC TCP endpoint.
- `scripts/plc_soft_reset.sh`: more robust qcaspi reload with auto iface detection.

Open item
- EV closed TCP shortly after first CurrentDemand; collect BMS snapshot and CP monitor traces to decide if this is power‑stage behavior, EV expectation mismatch, or a transient CP event.

---

## 16) BMS Snapshot Logging Fix + End‑to‑End Capture (Sep 17, 2025)

Symptom
- Despite reaching `CurrentDemand`, the JSON log did not contain structured `"name":"hlc","msg":"ISO15118 state"` lines with `bms{}`/`evse{}` fields, so `wait_bms.py` never produced `/tmp/evse_bms_snapshot.json`.

Root cause
- The SECC’s session state machine only updated observers (EVSE controller) under a narrow path; on some transitions, the state notification wasn’t emitted, so the HAL controller couldn’t log the consolidated `ISO15118 state` snapshot.

Fix
- Always notify the EVSE controller of the present protocol state after each message is processed.
- File: `src/iso15118/iso15118/shared/comm_session.py`
- Change: move the `await self._update_state_info(self.current_state)` out of the logging exception path so it always runs.

Effect
- On every state transition, a structured JSON line is logged (logger `hlc`, `msg="ISO15118 state"`) including:
  - `iso_state` (e.g., CableCheck, PreCharge, CurrentDemand)
  - `bms`: `present_soc`, `present_voltage`, `target_voltage`, `target_current`, session limits (e.g., `max_current_limit`)
  - `evse`: measured voltage/current and last commanded setpoints
- `scripts/wait_bms.py` now finds the first such entry and writes `/tmp/evse_bms_snapshot.json` automatically.

How to capture the snapshot
```bash
# Environment
export EVSE_ID=INJPSE0006360
export PLC_IFACE=eth1
export ESP_CP_PORT=/dev/ttyACM0
export SECC_CONFIG_PATH=$PWD/secc.env
export SLAC_CONFIG_PATH=$PWD/slac.env
export EVSE_CP_HOST_HINTS=1
export EVSE_HAL_ADAPTER=esp-uart   # or esp-periph
export EVSE_LOG_FORMAT=json
export EVSE_LOG_FILE=/tmp/evse_run.jsonl

# Launch
scripts/start_evse_hal.sh --evse-id "$EVSE_ID" --iface "$PLC_IFACE" --port "$ESP_CP_PORT" --adapter "$EVSE_HAL_ADAPTER" &

# Wait for BMS snapshot
python scripts/wait_bms.py --log /tmp/evse_run.jsonl --timeout 120 && cat /tmp/evse_bms_snapshot.json
```

Automated wrapper
```bash
EVSE_TEE_JSON=/tmp/evse_run.jsonl \
scripts/run_until_bms.sh --evse-id "$EVSE_ID" --iface "$PLC_IFACE" --port "$ESP_CP_PORT" --adapter "$EVSE_HAL_ADAPTER" --attempts 2 --run-secs 220
```

DIN/bench fallback (if contactor AUX or DC stage isn’t finalized)
- Temporarily simulate contactor closure to carry HLC through CableCheck → CurrentDemand:
  ```bash
  export EVSE_HAL_ADAPTER=esp-periph
  export ESP_PERIPH_PORT=/dev/ttyACM0
  export EVSE_SIM_CONTACTOR=1
  ```
  Then rerun the launcher and capture the snapshot as above.

Current status
- SLAC and HLC consistently reach `CurrentDemand`.
- EV occasionally drops TCP right after first CurrentDemand; structured `ISO15118 state` logging is in place to capture the targets/measured values for analysis.
- Next step: use the captured snapshot to compare EV targets vs EVSE delivery and correlate with CP/contactor events.
