# DC Single GUN PLC for Raspberry Pi

This project provides a Python-based implementation of the ISO 15118 and SLAC protocols for a single-gun DC charger, designed to run on a Raspberry Pi.
> **Note:** This repository uses Git submodules. After cloning, run `git submodule update --init --recursive` to download them.

## Features

*   **ISO 15118-2 and ISO 15118-20 compliant:** Supports both AC and DC charging, as well as Plug & Charge (PnC).
*   **SLAC protocol support:** Implements the SLAC protocol for establishing a communication link between the EV and EVSE.
*   **Modular and extensible:** The project is designed to be easily extended and customized for different hardware and use cases.
*   **Raspberry Pi compatible:** The project is optimized for running on a Raspberry Pi, making it a cost-effective solution for EVSE development.

## Architectural Overview

`src/evse_main.py` orchestrates the charger logic. It binds
[`pyslac`](src/pyslac) and the ISO 15118 stack provided by the
[`iso15118`](https://pypi.org/project/iso15118/) package directly to a
standard network interface (for example `eth0`). Once a vehicle is
matched via SLAC, ISO 15118 communication continues on the same
interface. Each component is replaceable, enabling custom hardware
front‑ends or SECC implementations.

### HAL Adapters: Which parts are hardware vs simulated?

The EVSE HAL exposes five primitives: CP reader, PWM, contactor, DC supply (rectifier), and energy meter. Two adapters are provided:

- `esp-uart` (CP helper only)
  - Hardware: CP reader + PWM via ESP32‑S3 CP firmware (UART JSON).
  - Simulated: contactor, DC supply, energy meter (in‑process simulation).
  - Use for protocol bring‑up (SLAC/ISO phases). Real DC power is not switched; EV will usually abort at CableCheck/PreCharge without a real DC path.

- `esp-periph` (CP + contactor + DC + meter)
  - Hardware: CP reader/PWM, contactor control, DC set/enable, meter reads via ESP32‑S3 peripheral coprocessor firmware.
  - Use for real DC charging. Wire contactor AUX, DC rectifier control, and meter to the ESP periph firmware.

Bench toggles (optional):
- `EVSE_SIM_CONTACTOR=1` – treat contactor as always closed (bench only).
- `EVSE_SIM_SUPPLY=1` – present voltage/current mirror last setpoints in logs (for testing logic only; EV still sees the real bus).

## Getting Started

### Prerequisites

*   Raspberry Pi 3 or 4
*   Python 3.7+
*   pip

### Installation

1.  Clone the repository:

```
git clone https://github.com/joulepoint/dc-plc.git
cd dc-plc
```

2.  Initialize Git submodules:

```
git submodule update --init --recursive
```

3.  Install the dependencies:

```
python3 -m pip install -r requirements.txt
python3 -m pip install iso15118
python3 -m pip install -e src/pyslac --no-deps
python3 -m pip install -r requirements-submodules.txt
```

Installing the `iso15118` package via pip allows the startup scripts to import
it directly without modifying `sys.path`.

4.  Generate the test certificates (idempotent):

```
./scripts/generate_certs.sh
```

### Quick plug-and-play setup

For a turnkey Raspberry Pi configuration the repository provides a helper
script that installs dependencies and initialises git submodules.

```bash
sudo ./setup_rpi.sh
sudo reboot
source /opt/evse-venv/bin/activate
python src/evse_main.py --evse-id <EVSE_ID>
```

Troubleshooting tips and a flow diagram of the process are available in
[docs/plug_and_play.md](docs/plug_and_play.md).

### End-to-End Quickstart (ESP32‑S3 CP + HAL + ISO 15118)

This is the shortest complete path from firmware to a running SECC with logs that include BMS demands and EVSE measurements per message.

1) Build and flash the ESP32‑S3 CP firmware

```bash
# Build
python3 -m platformio run -d firmware/esp32s3_cp
# Flash (adjust port for your OS, e.g., /dev/ttyACM0, /dev/cu.usbmodem*)
python3 -m platformio run -d firmware/esp32s3_cp -t upload --upload-port /dev/ttyACM0
```

Notes:
- Radios (Wi‑Fi/BLE) are disabled at boot in firmware to reduce ADC jitter.
- Robust top‑K ADC sampling + hysteresis are enabled. Tunables below.

2) Prepare Python env on the Pi/host

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt -r requirements-submodules.txt
# Local pyslac editable install (no external pip needed)
python3 -m pip install -e src/pyslac --no-deps
```

3) Set runtime parameters and run in HAL mode

```bash
export EVSE_CONTROLLER=hal
export EVSE_HAL_ADAPTER=esp-uart          # or esp-periph when using the peripheral coprocessor
export ESP_CP_PORT=/dev/serial0           # or /dev/ttyAMA0, /dev/ttyUSB0
export EVSE_LOG_FORMAT=json               # optional: structured logs with BMS/EVSE snapshots

# Optional: point to certificates dir if not default
# export PKI_PATH=$(pwd)/pki

python src/evse_main.py \
  --evse-id EVSE-1 \
  --iface eth0 \
  --slac-config slac.env \
  --secc-config secc.env \
  --controller hal

# For real DC power with ESP peripheral (contactor + rectifier + meter):
# scripts/start_evse_hal.sh auto-detects PLC iface and sets IPv6 LL.
EVSE_TEE_JSON=/tmp/evse_e2e.jsonl \
scripts/start_evse_hal.sh --evse-id EVSE-1 --adapter esp-periph --port /dev/ttyACM0 --json /tmp/evse_e2e.jsonl
```

What you’ll see:
- SLAC match on `--iface` then “Launching ISO 15118 SECC”.
- Per‑message ISO logs (`hlc` logger) with fields:
  - `iso_state` (e.g., CableCheck, PreCharge, CurrentDemand)
  - `bms`: `present_soc`, `target_voltage`, `target_current`, session limits

### Fast Loop + Verification (CurrentDemand, 250 ms budget)

This repo includes a fast, robust CurrentDemand loop with two modes and tools to prove timing/coherency:

- Firmware telemetry at 100 ms
  - The ESP32‑S3 peripheral polls V/I every 100 ms using the module’s fast channels. Status/Hi/Lo at 1 s.
  - Build: `python3 -m platformio run -d firmware/esp32s3_cp`

- Field vs bench reply shaping
  - Field (recommended for vehicles): `EVSE_ECHO_CURRENTDEMAND=0`
    - CurrentDemand “present” values come from measured V/I (safe for EV sanity checks).
  - Bench echo (rigs that follow your reply): `EVSE_ECHO_CURRENTDEMAND=1`
    - Present values shaped from EV targets, clamped by I/P/V limits (keeps SECC mismatch quiet during bring‑up).

- Pre‑commit setpoints
  - Pi sends V+I atomically via `dc.set` (coalesced). Optional tiny latch wait, `EVSE_CD_PRECOMMIT_WAIT_MS=15` (0–20 ms).

- CP stability
  - Hold C/D for short dips during CurrentDemand: `EVSE_CP_STICKY_MS=600`. Emergency E/F always triggers shutdown.

- Optional uvloop (Unix)
  - Faster asyncio loop for SECC/SLAC. Installed automatically when `uvloop` is present (pip) and `USE_UVLOOP` not `0`.

- Log verification
  - `python scripts/verify_session_logs.py /path/to/evse.log`
    - Reports CurrentDemand latency p95/p999 (if markers present), CP transitions, mismatch counts, and EV inter‑request period stats.

Environment knobs (put in `secc.env` or export before run):
- CurrentDemand shaping and speed
  - `EVSE_ECHO_CURRENTDEMAND=0|1` (field|bench)
  - `EVSE_FAST_HARD_APPLY=1` (pre‑commit fast)
  - `EVSE_CD_PRECOMMIT_WAIT_MS=15` (set 0 if close to 250 ms budget)
  - `EVSE_PERIPH_CFG_RAMP_I=70`, `EVSE_PERIPH_CFG_RAMP_V=200` (ESP slew)
  - (bench echo only) `EVSE_ECHO_I_FLOOR_A=1.0`, `EVSE_ECHO_I_FLOOR_FRAC=0.05`, `EVSE_ECHO_I_MAX_A=200`, `EVSE_ECHO_P_MAX_W=30000`
- CP robustness
  - `EVSE_CP_STICKY_MS=600` (hold C/D for short dips)
- Optional per‑cycle metrics
  - `EVSE_CD_LOG=1` (emit `cd_tick` lines with measured vs requested power)
- Optional uvloop (Unix)
  - `pip install uvloop` and keep `USE_UVLOOP` unset or `1` (default). Set `USE_UVLOOP=0` to disable.

Testing the loop:
- Unit tests: `pytest -q tests/test_hal_currentdemand_echo.py`
  - Verifies 30 kW clamp (~100 A @ 300 V) and CP sticky behavior.
- Log verification: `python scripts/verify_session_logs.py /path/to/evse.log`

### Field Bring‑Up Summary (DC CCS2)

With a real vehicle connected and a QCA7000 (qcaspi) PLC on the Pi:

- Detect the PLC NIC (driver `qcaspi`), often `eth1`:
  - `for i in /sys/class/net/*; do d=$(basename $i); ethtool -i $d 2>/dev/null | awk -v d=$d -F': ' '/driver/{print d, $2}'; done`
- Run the HAL launcher (auto‑adds IPv6 link‑local and starts SECC after SLAC match):
  ```bash
  export EVSE_ID=INJPSE0006360
  export ESP_CP_PORT=/dev/ttyACM0
  export EVSE_CP_HOST_HINTS=1
  export SECC_CONFIG_PATH=$PWD/secc.env
  export SLAC_CONFIG_PATH=$PWD/slac.env
  EVSE_TEE_JSON=/tmp/evse_e2e.jsonl \
  scripts/start_evse_hal.sh --evse-id "$EVSE_ID" --iface eth1 --port "$ESP_CP_PORT" --adapter esp-uart
  ```
- Expected sequence (seen in logs):
  - SLAC: CM_SET_KEY → ATTEN → MATCHED (EV MAC printed)
  - ISO 15118: SessionSetup → ServiceDiscovery → Authorization → CPD → CableCheck → PreCharge
  - PowerDelivery(Start) → CurrentDemand

If the EV closes TCP shortly after CurrentDemand (IncompleteReadError on our side), capture a structured tee and inspect the first BMS snapshot to verify targets vs EVSE delivery:

```bash
python scripts/wait_hlc_phases.py --log /tmp/evse_e2e.jsonl --timeout 0
python scripts/wait_bms.py --log /tmp/evse_e2e.jsonl --timeout 0
cat /tmp/evse_bms_snapshot.json
```

### BMS Snapshot Capture (End‑to‑End)

To persist a one‑line JSON snapshot of the EV’s BMS targets (and EVSE measured/set values) once CurrentDemand starts:

- Use JSON logs or a JSON tee and launch in HAL mode:
  ```bash
  export EVSE_ID=INJPSE0006360
  export PLC_IFACE=eth1
  export ESP_CP_PORT=/dev/ttyACM0
  export SECC_CONFIG_PATH=$PWD/secc.env
  export SLAC_CONFIG_PATH=$PWD/slac.env
  export EVSE_CP_HOST_HINTS=1
  export EVSE_HAL_ADAPTER=esp-uart       # or esp-periph
  export EVSE_LOG_FORMAT=json
  export EVSE_LOG_FILE=/tmp/evse_run.jsonl
  scripts/start_evse_hal.sh --evse-id "$EVSE_ID" --iface "$PLC_IFACE" --port "$ESP_CP_PORT" --adapter "$EVSE_HAL_ADAPTER"
  ```
- In another terminal, wait for the snapshot and print it:
  ```bash
  python scripts/wait_bms.py --log /tmp/evse_run.jsonl --timeout 120 && cat /tmp/evse_bms_snapshot.json
  ```

If you prefer a wrapper that auto‑stops when the snapshot appears, use `scripts/run_until_bms.sh`, e.g.:

```bash
EVSE_TEE_JSON=/tmp/evse_run.jsonl \
scripts/run_until_bms.sh --evse-id "$EVSE_ID" --iface "$PLC_IFACE" --port "$ESP_CP_PORT" --adapter "$EVSE_HAL_ADAPTER" --attempts 2 --run-secs 220
```

Expected snapshot fields:
- `bms.present_voltage`, `bms.target_voltage`, `bms.target_current`, `bms.max_current_limit`, `bms.evcc_id`
- `evse.present_voltage`, `evse.present_current`, `evse.set_voltage`, `evse.set_current`, `evse.rated_max_current`, `evse.rated_max_voltage`

Troubleshooting when snapshot does not appear:
- Ensure you’re on the current code: the SECC session now always emits a state notification that the HAL logs as `"name":"hlc","msg":"ISO15118 state"` lines. These carry the `bms` and `evse` objects. If you do not see them, rebuild/restart.
- If the EV disconnects right after the first CurrentDemand, verify contactor/AUX and DC stage behavior (voltage/current tracking). You can temporarily set `EVSE_SIM_CONTACTOR=1` (with `esp-periph`) for bench verification.

Adapter implications for power delivery
- With `esp-uart`: contactor/DC/meter are simulated; the EV sees the actual bus (likely 0 V) and will fail CableCheck/PreCharge. Use this mode to validate SLAC/ISO flows only.
- With `esp-periph`: the HAL commands real contactor and DC setpoints through the ESP peripheral. Ensure wiring to the rectifier and meter is correct; HLC will proceed to CurrentDemand when PreCharge targets are met.

### Troubleshooting QCA7000 (qcaspi)

- Health check: `bash scripts/qca_health.sh` (shows driver, overlay, dmesg, iface stats)
- Robust soft‑reset (auto‑detect iface, pluggable=1 by default):
  ```bash
  export EVSE_PLC_SOFT_RESET=1
  export QCASPI_PLUGGABLE=1 QCASPI_CLKSPEED=8000000 QCASPI_BURST=5000
  scripts/start_evse_hal.sh --evse-id EVSE-1 --port /dev/ttyACM0 --adapter esp-uart
  ```
- Persist options: `/etc/modprobe.d/qcaspi.conf` → `options qcaspi qcaspi_pluggable=1 qcaspi_clkspeed=8000000 qcaspi_burst_len=3000`

### No‑Vehicle Test Tools

- `scripts/start_secc_only.py` – start SECC on an interface without SLAC (bench testing)
- `scripts/send_sdp.py` – broadcast SDP over IPv6/UDP and print the SECC TCP endpoint
- `scripts/evcc_min_flow.py` – minimal EVCC client for SAP → SessionSetup → ServiceDiscovery

### Productionization Checklist (DC)

- Systemd services for HAL/SECC; order after serial and qcaspi ready
- udev rules for stable `/dev/tty*` and permissions
- IPv6 link‑local on PLC iface (don’t disable IPv6)
- CPU governor=performance; log rotation and durable log path
- Certificates and valid EVSEID; manage `pki/`
- Contactor coil + AUX prove‑out; DC supply control + meter integration (move from sim to real driver)
- Thermal monitoring and derating
  - `evse`: `present_voltage`, `present_current`, `set_voltage`, `set_current`

4) Optional smoke checks

```bash
# SECC timeout safe‑stop smoke test
python scripts/secc_timeout_smoke.py

# Simulated end‑to‑end charging (no hardware power electronics)
python src/ccs_sim/orchestrator.py
```

### System Hardening + Env (Wi‑Fi kept)

For a hardened runtime with Bluetooth/Avahi/CUPS/ModemManager disabled, a
deterministic interface fallback (eth0→eth1→en*→wlan0), and a managed project
.env you can update and re‑export, use:

```
# Create .env with smart defaults and generate an export script
scripts/evse_setup.sh env init
scripts/evse_setup.sh env export
source scripts/export_env.sh    # export into current shell

# Detect a better primary interface and update .env
scripts/evse_setup.sh iface update-env

# Harden services (keeps Wi‑Fi stack; web servers kept by default)
sudo scripts/evse_setup.sh services
# Options:
#   --disable-web     also disable nginx/apache/lighttpd
#   --keep-serial     keep serial-getty (not recommended; it can lock the ESP UART)

# Or do it all in one go (Linux): init env, export, deps, venv, harden, port check
sudo scripts/evse_setup.sh all
```

Notes:
- The hardening step disables: bluetooth, hciuart (RPi), avahi‑daemon, ModemManager,
  CUPS, common serial getties (ttyAMA0/ttyS0/ttyUSB0), and common web servers
  (nginx/apache2/lighttpd). Wi‑Fi and core networking are preserved.
- The script is idempotent and safe to re‑run. It prints active TCP listeners
  afterwards so you can verify no unexpected services are holding ports.
- To persist environment globally for new shells: `sudo scripts/evse_setup.sh env apply-global`.

## Boot Process

The system brings up a charging session in the following stages:

1. **Setup script** – [`setup_rpi.sh`](setup_rpi.sh) installs dependencies
   and updates submodules.
2. **SLAC** – [`pyslac`](src/pyslac) matches the vehicle and establishes a
   powerline link on the chosen network interface.
3. **ISO 15118 session** – once matched, the SECC from the `iso15118`
   package negotiates charging parameters with the EV.

## Usage

The `evse_main.py` helper in `src/` performs SLAC matching using
`pyslac` and, once matched, launches the ISO 15118 SECC bound to the
same network interface.

```
python src/evse_main.py --evse-id <EVSE_ID> \
    --slac-config path/to/pyslac.env \
    --secc-config path/to/secc.env \
    --cert-store pki \
    --iface eth0
```

* `--slac-config` – optional path to a PySLAC `.env` file
* `--secc-config` – optional path to an ISO 15118 SECC `.env` file
* `--cert-store` – directory containing ISO 15118 certificates (`PKI_PATH`), defaults to `pki`
* `--iface` – network interface used for both SLAC and ISO 15118 (default `eth0`)

## Configuration and Certificates

Environment variables drive both `pyslac` and the ISO 15118 SECC. Create
two `.env` files and point `evse_main.py` at them with the
`--slac-config` and `--secc-config` flags:

```bash
# pyslac.env
IFACE=eth0

# secc.env
IFACE=eth0
EVSE_ID=DE*PNC*E12345*1
```

Additional options are documented in the respective packages. Certificates
for Plug & Charge are generated with

## ESP32‑S3 Peripheral: CP + DC Module Control over CAN (MCP2515)

The ESP32‑S3 peripheral firmware provides Control Pilot (CP) handling, contactor control, and Maxwell ENR DC module control over CAN (via the MCP2515). The Pi communicates with the ESP over UART using a simple JSON/JSON‑RPC protocol.

### Hardware & Wiring

- CP PWM out: `GPIO38` (LEDC 1 kHz)
- CP ADC in: `GPIO1` (12‑bit ADC)
- UART to Pi: RX=`GPIO44`, TX=`GPIO43` (115200 baud)
- Contactor coil: PCA9555 P01 (I2C expander); AUX input optional
- MCP2515 (CAN 2.0B, extended 29‑bit): 125 kbit/s
  - CS=`GPIO41`, RST=`GPIO40`, SCK=`GPIO48`, MOSI=`GPIO47`, MISO=`GPIO21`, INT=optional GPIO
  - Use a 3.3 V CAN transceiver (e.g., SN65HVD230). If using 5 V TJA1050 boards, add level shifting or replace transceiver.

Build flags are set in `firmware/esp32s3_cp/platformio.ini`:

```
-DCAN_CS_PIN=41  -DCAN_RST_PIN=40  -DCAN_SCK_PIN=48  -DCAN_MOSI_PIN=47  -DCAN_MISO_PIN=21
-DMCP2515_CLK_MHZ=8  # match 8 MHz/16 MHz crystal on your MCP2515 board
```

### Build, Flash, Monitor

```
cd firmware/esp32s3_cp
python3 -m platformio run
python3 -m platformio run -t upload --upload-port /dev/ttyACM0
python3 -m platformio device monitor -b 115200
```

### Firmware Protocol (UART)

The firmware streams CP `status` objects at ~5 Hz and accepts JSON‑RPC requests:

```
{"type":"req","id":"<uuid|string|int>","method":"<name>","params":{...}}
{"type":"res","id":"...","ts":<ms>,"result":{...}} or {"error":{...}}
{"type":"evt","method":"evt:<name>","result":{...}}
```

Supported methods (subset):

- System: `sys.ping`, `sys.info`, `sys.set_mode {mode:"sim|hw"}`, `sys.arm`
- Contactor: `contactor.set {on}`, `contactor.check`
- Meter/Temps: `meter.read`, `meter.stream_start|stop`, `temps.read`, `temps.stream_start|stop`
- DC over CAN (Maxwell ENR):
  - `dc.discover` → scan modules (broadcast Read Status)
  - `dc.set {v:float_V, i:float_A}` → soft‑ramp targets
  - `dc.enable {on}` → gate by CP C/D and contactor AUX
  - `dc.status` → setpoints + per‑module telemetry (`v_mv`, `i_ma`, `st`)
  - `dc.estop` → immediate off + opens contactor
  - `dc.set_hilo {mode:1|2|3}` → optional Hi/Lo/Auto

### Maxwell CAN Mapping (summary)

- Extended 29‑bit ID: `[28:25]=1 | [24:21]=monitor(1) | [20:14]=module(0=broadcast)`
- Set Vref: `Byte0=(grp<<4)|0x00`, `Byte1=0x02`, `Byte4..7=Vref_mV (MSB..LSB)`
- Set Ilim: `Byte1=0x03`, `Byte4..7=Ilim_mA (MSB..LSB)`
- Power On/Off: `Byte1=0x04`, `Byte4..7=0 (On)/1 (Off)`
- Read: `Byte0=(grp<<4)|0x02`, `Byte1=0x00 V / 0x01 I / 0x08 Status`
- AllSetData (sync): `Byte0=(grp<<4)|0x0B`, `Byte1=On/Off+Hi/Lo`, `Byte2..3=I(0.1A)`, `Byte4..5=Vbat(0.1V)`, `Byte6..7=Vout(0.1V)`

### End‑to‑End Control Flow

```mermaid
flowchart TD
  A[CP A→B detected] --> B(SLAC match over PLC)
  B --> C{SECC PowerDelivery Start}
  C -->|set_hlc_charging(True)| D[Close contactor]
  D --> E[dc.enable on]
  E --> F{Soft‑ramp}
  F -->|AllSetData sync| G[CurrentDemand loop]
  G --> H{PowerDelivery Stop}
  H --> I[dc.enable off + soft‑stop]
  I --> J[Open contactor]
```

### Pi Integration (HAL adapter)

Use the `esp_periph` adapter to forward SECC setpoints to the ESP and control the contactor:

```
export EVSE_CONTROLLER=hal
export EVSE_HAL_ADAPTER=esp_periph
export ESP_PERIPH_PORT=/dev/ttyUSB0
python src/evse_main.py --iface eth1 --evse-id EVSE-1 --secc-config secc.env
```

### Safety & Test Checklist

- Verify CP: mode is `dc`, state transitions A/B/C visible; radios are off.
- Verify contactor AUX OK before enabling DC.
- Verify CAN bitrate (125 kbit/s) and MCP2515 crystal (8/16 MHz).
- Bench: `sys.arm` → `contactor.set on` → `dc.discover` → `dc.set v/i` → `dc.enable on` → `dc.status`.

## Scripts Overview

The `scripts/` folder contains helper tools for bring‑up, health checks, ESP control, and smoke tests. Below is a curated map with what, why, and how to use each.

### Bring‑Up & Health

- `plc_soft_reset.sh`: Reloads the QCA7000 PLC driver and rebinds SPI safely.
  - Why: Recover from PLC stalls; re‑establish `eth1` (qcaspi) without reboot.
  - Use: `sudo bash scripts/plc_soft_reset.sh`

- `qca_health.sh`: Summarizes PLC health: module info, overlays, dmesg, driver stats.
  - Why: One‑shot sanity check that the QCA7000 is detected and healthy.
  - Use: `bash scripts/qca_health.sh`

- `sniff_ev_mac.py`: Passive HomePlug AV sniffer to extract EV MAC during SLAC.
  - Why: Debug SLAC timing and matching; verify EV presence on the PLC link.
  - Use: `sudo -E python scripts/sniff_ev_mac.py --iface eth1 --timeout 60`

- `start_evse_hal.sh`: Convenience launcher for the SECC in HAL mode with iface detection.
  - Why: Run the ISO 15118 SECC with minimal env setup.
  - Use: `bash scripts/start_evse_hal.sh`

- `evse_setup.sh`, `export_env.sh`, `generate_certs.sh`:
  - Why: Setup machine, manage .env, and generate ISO 15118 PKI.
  - Use: `sudo scripts/evse_setup.sh all` or `scripts/evse_setup.sh env init && scripts/evse_setup.sh env export && source scripts/export_env.sh` and `./scripts/generate_certs.sh`

### ESP Peripheral Tools

- `esp_periph_cli.py`: JSON‑RPC CLI for ESP32‑S3 peripheral firmware over UART.
  - Why: Direct control for contactor, discovery, setpoints, enable, status.
  - Use: `python scripts/esp_periph_cli.py --port /dev/ttyUSB0 discover | jq .`
  - Subcommands: `ping`, `info`, `arm`, `contactor 0|1`, `discover`, `set --v V --i A`, `enable 0|1`, `status`, `estop`.

- `module_test.sh`: End‑to‑end DC module exercise (ramp up/down).
  - Why: Exercise discovery → contactor → enable → setpoints ramp 50→500 V in 10 V steps every 3 s, then ramp down.
  - Use: `PORT=/dev/ttyUSB0 START_V=50 END_V=500 STEP_V=10 DWELL_S=3 CURRENT_A=10 bash scripts/module_test.sh`
  - Behavior: Arms/Closes contactor, enables DC, ramps up, prints summarized status (`esp_status_summary.py`), ramps down, disables and opens contactor (trap on EXIT).

- `esp_status_summary.py`: jq‑free summarizer for `dc.status` JSON.
  - Why: Quick readout (enabled, Vset/Iset, Vavg/Isum, module count, fault words).
  - Use: `python scripts/esp_periph_cli.py --port /dev/ttyUSB0 status | python scripts/esp_status_summary.py`

### ESP/SLAC/SECC Smokes & Demos

- `esp_slac_smoke.py`, `esp_ac_pwm.py`, `esp_periph_demo.py`: targeted bring‑up helpers for ESP CP and AC PWM.
- `secc_*_smoke.py`: SECC timing/duplication/timeout smoke tests.
- `sim_e2e_cp_flow_test.py`: Full end‑to‑end CP/charging flow in simulation (no HV).

### Docker/RPi Helpers

- `pi_docker_e2e.sh`: Build/run end‑to‑end in Docker on Pi.
- `rpi0_*`: Build/test/run helpers for Raspberry Pi Zero environments.

Tip: Most scripts print their assumptions and are idempotent where possible. Prefer running with `bash -x` during bring‑up to trace steps.

### IDE Tips (import resolution)

If your IDE reports missing imports for `iso15118.*` modules, add the following to your workspace settings so static analysis can find the submodule path:

- For VS Code (`.vscode/settings.json`):
  ```json
  {
    "python.analysis.extraPaths": [
      "src",            
      "src/iso15118"    
    ]
  }
  ```
The runtime already adjusts `sys.path` to include `src/iso15118`, but static analyzers need the hint.

[`scripts/generate_certs.sh`](scripts/generate_certs.sh) and stored under
`pki/` by default.

### Robustness Controls (optional)

To make sessions more resilient to transient PLC corruption and packet loss, a few
environment variables can be set in `secc.env`:

- `V2G_DUPLICATE_RESEND_WINDOW_S`: Time window to treat byte‑identical requests as duplicates and resend the last response (default `2.0`).
- `V2G_DUPLICATE_RESEND_MAX`: Max number of duplicate resends within the window (default `3`).
- `V2G_DUPLICATE_RESEND_ENABLED`: Enable/disable duplicate‑resend behavior (default `1` → enabled).
- `V2G_MAX_DECODE_ERRORS`: Number of EXI decode/validation errors tolerated before aborting the session (default `2`).
- `V2G_DROP_TX_PROB`: Simulation only. Probability [0.0–1.0] to drop outgoing responses to test EV retransmission behavior (default `0.0`).
- `V2G_MAX_EXI_BYTES`: Cap raw EXI payload length in bytes (default `262144`). Set `0` to disable.
- `V2G_MAX_EXI_JSON_BYTES`: Cap decoded EXI JSON length in bytes (default `1048576`). Set `0` to disable.

Protocol selection (SAP)
- `SECC_SAP_PREFER_EV_PRIORITY`: If `1` (default), the SECC honors the EV’s priority list from SupportedAppProtocol. If `0`, the SECC prefers its configured protocol order (`PROTOCOLS` in `secc.env`), which can improve fail‑safety by selecting DIN 70121 first when both DIN and ISO 15118 are offered.

These controls do not change protocol semantics and are safe defaults. Increase/decrease per site as needed based on PLC link quality.

Control Pilot robustness (HAL)
- `CP_DEBOUNCE_S`: Debounce window for CP state changes (seconds). New CP states A/B/C/D must remain stable for this duration before the HAL reports them. Emergency states `E`/`F` bypass debounce for immediate fail‑safe reaction. Default `0.05` (50 ms).
- `SECC_CP_DISCONNECT_IMMEDIATE_CUTOFF_S`: Immediate contactor open on CP disconnect at the host level (seconds). Default `0.1` (100 ms). Set to `0` to disable host‑enforced cutoff.

### Power Delivery Mismatch Detection

To detect and mitigate power delivery mismatches (EV request vs EVSE delivery), tune the following in `secc.env`:

- `SECC_PRECHARGE_TOL_V`: Precharge voltage tolerance in volts (default `20.0`).
- `SECC_PRECHARGE_TIMEOUT_S`: Max time to reach precharge target (default `10.0`).
- `SECC_STEADY_V_TOL_FRAC`: Allowed steady-state voltage deviation fraction (default `0.05`).
- `SECC_STEADY_I_TOL_FRAC`: Allowed steady-state current deviation fraction (default `0.05`).
- `SECC_MISMATCH_GRACE_S`: Grace period before warning on mismatch (default `0.5`).
- `SECC_MISMATCH_ABORT_S`: Abort after persistent mismatch beyond tolerance (default `2.0`).
- `SECC_MIN_CURRENT_FOR_CHECK_A`: Skip current mismatch checks below this current to avoid noise (default `2.0`).

Behavior:
- During PreCharge, if the EVSE cannot reach the EV’s requested voltage within tolerance and before timeout, the SECC aborts safely without closing the contactor.
- During CurrentDemand, the SECC compares measured EVSE voltage/current against EV targets each loop. Persistent deviations result in a controlled stop to protect the EV and EVSE. Logged counters summarize any warnings/aborts.

### EVCC Fault‑Injection Helper

Use the helper to inject malformed frames or duplicates against a running SECC:

```
python scripts/evcc_fault_injector.py --host 127.0.0.1 --port 65000 --mode corrupt-exi --count 3 --size 64
python scripts/evcc_fault_injector.py --host 127.0.0.1 --port 65000 --mode duplicate --payload-hex DEADBEEF
```

Options:
- `--mode`: `corrupt-exi` (valid V2GTP header, random payload), `duplicate` (send same frame repeatedly), `bad-header` (invalid header).
- `--protocol`: `iso2` or `v20` (default `iso2`), `--payload-type` (default `0x8001`).
- `--payload-hex` or `--size` to define EXI payload content.
- `--count` and `--interval` to repeat/intersperse traffic.

---

## BMS Snapshot Semantics (Why SoC can be 0/None)

Background
- The EV only communicates SoC and DC targets during PreCharge/CurrentDemand.
  Earlier phases (SAP, SessionSetup, ServiceDiscovery, Authorization, CPD) do
  not carry SoC.

What happened
- Early state-change logs used to include a `bms` object with placeholder
  values (null/0.0). If a session ended before PreCharge/CurrentDemand,
  operators saw zeros and assumed “SoC=0”.

Fixes
- We now emit `bms` only when it’s meaningful (SoC present or non-zero
  target voltage/current). Before that, the `bms` key is omitted.
- Added helper to extract the last valid BMS snapshot:
  - `python3 scripts/print_bms_snapshot.py /tmp/evse_e2e.jsonl`

How to get a valid snapshot
- Ensure a DC session proceeds to PreCharge/CurrentDemand.
- Use the launcher with JSON tee (example):
  ```bash
  EVSE_TEE_JSON=/tmp/evse_e2e.jsonl \
  scripts/start_evse_hal.sh --evse-id INJPSE0006360 --iface eth1 --port /dev/ttyACM0 --adapter esp-uart --json /tmp/evse_e2e.jsonl
  python3 scripts/print_bms_snapshot.py /tmp/evse_e2e.jsonl
  ```
- Bench-only: to simulate EVSE measurements without a DC stage, export
  `EVSE_SIM_SUPPLY=1` so reported present V/A mirror last setpoints.

---

## Edge Cases We Now Handle

- EV closes HLC TCP immediately: classified as “TCP peer closed connection”
  with session metrics; no stack traces.
- Duplicate EV requests: last response auto-resent within a short window.
- PLC interface readiness and IPv6: launcher ensures link-local and
  promisc/allmulti on the PLC netdev.

---

## September 2025 Stabilization: PLC reliability and SPI speed

What we changed
- Default PLC SPI clock reduced from 12 MHz to 8 MHz for qcaspi to improve stability on longer SPI runs and reduce “Bad signature” events.
  - `setup_rpi.sh` now writes `dtoverlay=qca7000,...,speed=8000000`.
  - `scripts/plc_soft_reset.sh` defaults to `QCASPI_CLKSPEED=8000000` and `QCASPI_BURST=5000`.
- Launcher (`scripts/start_evse_hal.sh`) improvements:
  - Prefers a PLC netdev driven by `qcaspi`/`qca7000` when auto‑detecting.
  - Heuristic auto soft‑reset when `ethtool -S` counters (resets/bad signature) suggest instability (can be disabled via `EVSE_PLC_SOFT_RESET_AUTO=0`).
  - New envs: `EVSE_PLC_SOFT_RESET_AUTO`, `EVSE_PLC_SOFT_RESET`, `EVSE_PLC_AUTO_SOFT_RESET_SPEED`, `EVSE_PLC_AUTO_SOFT_RESET_BURST`.
- SLAC:
  - CM_SET_KEY wait now honors `SLAC_INIT_TIMEOUT` from `slac.env`.
  - SetKey attempts prefer PLC‑capable ifaces first to avoid noise on non‑PLC NICs.

How to use
- Run with explicit PLC iface and pre‑run reset:
  ```bash
  EVSE_PLC_SOFT_RESET=1 PLC_IFACE=eth1 \
  EVSE_PLC_AUTO_SOFT_RESET_SPEED=8000000 EVSE_PLC_AUTO_SOFT_RESET_BURST=5000 \
  scripts/start_evse_hal.sh --evse-id INJPSE0006360 --adapter esp-periph --port /dev/ttyACM0 --json /tmp/evse_e2e.jsonl
  ```
- If counters keep increasing, try `EVSE_PLC_AUTO_SOFT_RESET_SPEED=6000000`.

Verification
- Check driver: `ethtool -i eth1 | grep -i '^driver'` → `qcaspi`.
- Check health: `ethtool -S eth1` → watch “Device resets”/“Bad signature”.
- Consistent IDs: `EVSE_ID` propagated to both SLAC and ISO; DIN hexBinary
  derived when needed.

Code references
- IncompleteRead handling: `src/iso15118/iso15118/shared/comm_session.py:722`
- Snapshot gating: `src/evse_hal/iso15118_hal_controller.py:580`
- JSON tee launcher: `scripts/start_evse_hal.sh`

Note: Duplicate‑resend works best when SECC has already sent at least one response in the session (so it has a last response to resend).

### EVCC Minimal Handshake

Drive a basic ISO 15118-2 handshake (SAP → SessionSetup → ServiceDiscovery) against a running SECC. Includes options to inject a duplicate ServiceDiscovery request and a corrupted frame after SAP.

```
python scripts/evcc_min_flow.py --host <SECC_IP> --port <SECC_TCP_PORT> \
  --duplicate-sd --corrupt-after-sap
```

This validates:
- End-to-end EXI encode/decode over TCP
- Duplicate request handling (resend of last response)
- Tolerance to corrupted frames (decode error path)
- SECC timeout handling (use `--pause-before-sd 3.0` to exceed sequence timeout if configured low)

### Metrics Export

On session stop, the SECC logs a single JSON line with counters and can optionally emit them via UDP for scraping:

- `V2G_METRICS_UDP`: `host:port` to emit one JSON datagram per session (optional).
- Counters include: `rx_decode_errors`, `rx_validation_errors`, `rx_invalid_v2gtp`, `dup_resent_count`, `dup_resend_enabled`, `dup_window_s`, `dup_resend_max`, `tx_drop_prob`.

## Troubleshooting and Hardware Notes

* Ensure the selected interface (default ``eth0``) exists and is
  connected.
* For wiring the PLC Stamp micro 2 via SPI to a Raspberry Pi refer to
  [docs/rpi_plc_pinout.md](docs/rpi_plc_pinout.md).
* Flow diagrams and additional tips live in
  [docs/plug_and_play.md](docs/plug_and_play.md).

### ESP32-S3 CP Helper (UART)

An optional ESP32-S3 firmware provides CP PWM generation and ADC sampling, exposing
status/control over a simple JSON‑over‑UART protocol.

- Firmware: `firmware/esp32s3_cp/` (PlatformIO; board: `esp32-s3-devkitc-1`)

Build/flash:

```bash
python3 -m platformio run -d firmware/esp32s3_cp
python3 -m platformio run -d firmware/esp32s3_cp -t upload --upload-port /dev/ttyACM0
```

Runtime behavior:
- CP is driven at 1 kHz. In DC‑AUTO mode: 5% duty in B/C/D; 100% in A/E/F.
- ADC sampler uses a top‑K average and band hysteresis to stabilize voltage state detection.
- Wi‑Fi/BLE is disabled during setup to reduce ADC jitter; if you must enable Wi‑Fi later, add power‑save off (`esp_wifi_set_ps(WIFI_PS_NONE)`).

Firmware tunables (set via `build_flags` or `#define`):
- `CP_TOPK_IN_BURST` (default 8): samples averaged for robust peak in a burst.
- `CP_SAMPLE_COUNT` (default 256): samples per burst; 192–320 typical.
- `CP_SAMPLE_DELAY_US` (default 10): inter‑sample delay; 8–12 typical.
- `CP_1_ADC_HYSTERESIS` (default 100 mV): bump to 150 mV if near boundaries.

Example PlatformIO override (append to `build_flags` in `firmware/esp32s3_cp/platformio.ini`):

```
build_flags =
  -DCP_TOPK_IN_BURST=8
  -DCP_SAMPLE_COUNT=256
  -DCP_SAMPLE_DELAY_US=10
  -DCP_1_ADC_HYSTERESIS=150
```

### Thermal Management (Derating + Faults)

The HAL EVSE controller implements a fail-safe thermal manager that can derate or stop DC charging based on temperature inputs and voltage sag heuristics. It also updates the EVSE’s advertised max current during the charge loop so the EV naturally tapers.

Environment variables (optional):

- `EVSE_THERMAL_WARN_<SENSOR>_C`: start of derating (C). Sensors: CONNECTOR, CABLE, RECTIFIER, AMBIENT. Defaults: 70/75/85/45.
- `EVSE_THERMAL_SHUTDOWN_<SENSOR>_C`: cutoff threshold (C). Defaults: 90/95/100/60.
- `EVSE_THERMAL_DERATE_START_<SENSOR>_C`, `EVSE_THERMAL_DERATE_END_<SENSOR>_C`: override linear derating window; default equals warn/shutdown.
- `EVSE_THERMAL_COOLDOWN_C`: temperature below which all sensors must cool to clear a fault latch. Default 50.
- `EVSE_THERMAL_FAULT_HOLD_S`: cooldown hold time before clearing fault. Default 30s.
- `EVSE_THERMAL_ENABLE_SAG`: enable voltage-sag-based inference. Default 1.
- `EVSE_THERMAL_SAG_FRAC`: fraction of target voltage considered excessive sag (e.g., 0.07 = 7%). Default 0.07.
- `EVSE_THERMAL_SAG_MIN_A`: minimum current for sag heuristic to apply. Default 50 A.
- `EVSE_THERMAL_SAG_DERATE`: fraction to reduce allowed current when sag detected (0..1). Default 0.5.

Live sensor inputs (optional, for integrations/tests):

- `EVSE_THERMAL_SENSOR_<SENSOR>_C`: publish live temperature readings (float). If not set, only sag inference applies.

Behavior:
- Above any shutdown threshold: charging is cut (contactor open) and a fault is latched until all sensors cool below `EVSE_THERMAL_COOLDOWN_C` for `EVSE_THERMAL_FAULT_HOLD_S`.
- Between derate start and end: current is linearly reduced; the EV is informed via a lower `EVSEMaxCurrentLimit` in ChargingStatusRes.
- Protocol: see `docs/esp_cp_uart_protocol.md`
- Python client: `src/evse_hal/esp_cp_client.py`
- HAL adapter (CP + PWM over UART, others simulated): set `EVSE_CONTROLLER=hal` and select adapter via `EVSE_HAL_ADAPTER=esp-uart`.

On Raspberry Pi, set `ESP_CP_PORT` (e.g., `/dev/serial0` or `/dev/ttyAMA0`) and ensure 115200 8N1. If unset, the client defaults to `/dev/serial0`. The CCS simulator (`src/ccs_sim/*`) will automatically use the HAL adapter selected via `EVSE_HAL_ADAPTER` (default `sim`). Example:

```
export ESP_CP_PORT=/dev/ttyAMA0
export EVSE_CONTROLLER=hal
export EVSE_HAL_ADAPTER=esp-uart
python src/evse_main.py --evse-id <EVSE_ID> --iface eth0
```

End-to-End DC setup (ESP CP + HAL)
- Flash `firmware/esp32s3_cp/` to ESP32‑S3 DevKitC‑1 (pins: PWM `GPIO38`, ADC `GPIO1`, UART RX `GPIO44`, TX `GPIO43`).
- Wire UART to Pi and CP to your EVSE CP frontend per hardware design.
- On the Pi, set `ESP_CP_PORT`, then run with `EVSE_CONTROLLER=hal` and `EVSE_HAL_ADAPTER=esp-uart`.
- The firmware enforces DC mode: CP is 100% (idle +12 V) in A/E/F and 5% in B/C/D.
- In `EVSE_CONTROLLER=hal` mode, `src/evse_main.py` waits for CP transitions from the ESP:
  - On `B` detected, it triggers SLAC; if `C/D`, it advances to `C`.
  - If CP returns to `A/E/F` before a match, it restarts waiting.
  - On SLAC match, it launches the ISO 15118 SECC on the selected interface.
- Use `GET /cp` to observe CP state/voltage and `/status` for session state; `/control/pwm` affects only sim or manual firmware mode.

Logging
- Configure unified logs with env vars:
  - `EVSE_LOG_LEVEL=DEBUG|INFO|...` (default INFO)
  - `EVSE_LOG_FORMAT=text|json` (default text)
  - `EVSE_LOG_FILE=/path/to/file.log` (optional)
- UART client logs TX/RX lines at DEBUG under logger `esp.cp`.
- Orchestrator emits event/periodic logs under `orchestrator`; precharge under `precharge`; API under `api`.
- Live view: when running the API server (`src/ccs_sim/fastapi_app.py`), use `GET /vehicle/live` to see CP voltage/state, SLAC status (incl. EV MAC if provided), ISO15118 protocol state, and BMS snapshot (target/present voltage/current, SoC).

## End-to-End With CCS BMS Simulator

This section documents a full, practical test loop using:

- Raspberry Pi + QCA7000‑class PLC over SPI (via transformer to the CP line)
- ESP32‑S3 for CP PWM/ADC (UART to the Pi)
- A CCS BMS simulator connected to the CP line

Hardware topology (simplified):

- Data path: `Pi → PLC (SPI) → Transformer → CP line → BMS Simulator`
- CP PWM/States: `Pi → ESP32‑S3 (UART) → CP line → BMS Simulator`

Prerequisites
- Run `sudo ./setup_rpi.sh` and reboot (enables qca7000 overlay, installs deps)
- Verify PLC overlay: `sudo scripts/qca_health.sh`
- Flash ESP32‑S3 firmware from `firmware/esp32s3_cp/` (PlatformIO)
- Wire ESP pins: PWM `GPIO38`, ADC `GPIO1`, UART RX `GPIO44`, TX `GPIO43`

Environment (on the Pi)

```bash
export ESP_CP_PORT=/dev/serial0   # or /dev/ttyAMA0
export EVSE_LOG_LEVEL=INFO        # or DEBUG
```

Option A: Full HAL run (recommended for real SLAC/ISO)

```bash
export EVSE_CONTROLLER=hal
export EVSE_HAL_ADAPTER=esp-uart
python src/evse_main.py --evse-id EVSE-1 --iface eth0
```

What to expect in logs:
- ESP USB logs show stable CP states and `mv_max/min/avg`, with occasional event logs (state transitions)
- `evse.main` logs:
  - “Vehicle detected via CP” once CP enters B/C/D
  - “SLAC matched” with fields: `ev_mac`, `nid`, `run_id`, `attenuation_db` (if available)
  - “Launching ISO 15118 SECC” and then ISO protocol state changes (logger `hlc`), including BMS snapshot fields: `present_voltage`, `target_voltage`, `target_current`, `present_soc`
- If SLAC doesn’t match within `SLAC_WAIT_TIMEOUT_S` (default 25 s), it emits a warning and sends an ESP “restart hint” (briefly leaves 5% then returns), then retries automatically.

Option B: API server (sim orchestration + live views)

```bash
python -m uvicorn src.ccs_sim.fastapi_app:app --host 0.0.0.0 --port 8000
```

Useful endpoints for manual checks:
- `GET /vehicle/live` → { cp, slac, iso15118, bms } snapshot
- `POST /esp/ping` → check Pi↔ESP link (“pong”: true)
- `POST /esp/restart_slac` → ask ESP to briefly leave 5% and return (nudges SLAC re‑init)
- `POST /esp/mode` {"mode":"dc|manual"} and `POST /esp/pwm` {"duty":5,"enable":true} for diagnostics

Automated smoke test (API)

```bash
python scripts/esp_slac_smoke.py --base http://localhost:8000 --timeout 30
```

This script:
- pings the ESP (`/esp/ping`)
- waits for CP state B/C/D via `/vehicle/live`
- tries SLAC matching (sim API) and times until MATCHED; on timeout, calls `/esp/restart_slac` and retries once
- prints a final `/vehicle/live` snapshot including CP, SLAC state, ISO state and BMS snapshot

Notes
- For a true end‑to‑end SLAC match with MAC/NID, prefer Option A (`evse_main.py`) so PySLAC runs for real.
- The API server provides observability and manual controls; it does not start PySLAC by itself.
- The ESP status JSON includes both `cp_mv` (instant peak) and `cp_mv_robust` (filtered peak used for state). The Pi client parses both.

### QCA7000 SPI Ethernet on Raspberry Pi

The script `setup_rpi.sh` now configures the Raspberry Pi to use the
in‑kernel `qcaspi` driver via the standard `qca7000` Device Tree
overlay. It:

- Enables SPI and adds `dtoverlay=qca7000,int_pin=25,speed=12000000` to the boot config
- Creates a post‑boot check to detect the `qcaspi` interface and bring it up via NetworkManager
- Installs an optional reset deassert service for `RESET_L` on BCM24

After running `sudo ./setup_rpi.sh` and rebooting, verify with:

```bash
sudo scripts/qca_health.sh
```

This prints module info, overlay lines in boot config, dmesg entries,
and the detected interface with driver details.

## Testing and Verification

Run the unit tests with [pytest](https://pytest.org/) to verify the
installation:

```bash
pip install -r requirements.txt
pytest
```

### End-to-End on Raspberry Pi

After running `setup_rpi.sh` and rebooting:

- Start the API simulation service:

  ```bash
  source /opt/evse-venv/bin/activate
  python -m uvicorn src.ccs_sim.fastapi_app:app --host 0.0.0.0 --port 8000
  ```

- In another shell, trigger a short session and inspect status:

  ```bash
  curl -fsS http://localhost:8000/hlc/status
  curl -fsS -X POST http://localhost:8000/start_session \
    -H 'Content-Type: application/json' \
    -d '{"target_voltage": 20, "initial_current": 15, "duration_s": 2}'
  watch -n 0.5 curl -fsS http://localhost:8000/status
  curl -fsS http://localhost:8000/meter
  ```

- To run the unified EVSE controller (SLAC + ISO 15118):

  ```bash
  sudo -s
  source /opt/evse-venv/bin/activate
  python src/evse_main.py --evse-id <EVSE_ID> --iface eth0 --controller sim
  # Or set EVSE_CONTROLLER=hal for HAL-backed control
  ```

### Docker on Raspberry Pi OS

To verify the Dockerized flow natively on a Raspberry Pi (no cross-build):

- Build, run, and smoke-test the API in one go:

  ```bash
  scripts/pi_docker_e2e.sh
  ```

- Or do it step-by-step:

  ```bash
  # Run tests during build via the test stage
  docker build --target test -t eco-rpi0:test -f docker/Dockerfile.rpi0 .

  # Build runtime image and run on host network
  docker build -t eco-rpi0:latest -f docker/Dockerfile.rpi0 .
  docker run --rm --network host -d --name eco-rpi0 eco-rpi0:latest

  # Smoke test
  curl -fsS http://127.0.0.1:8000/hlc/status
  curl -fsS -X POST http://127.0.0.1:8000/start_session \
    -H 'Content-Type: application/json' \
    -d '{"target_voltage":20, "initial_current":15, "duration_s":2}'
  curl -fsS http://127.0.0.1:8000/status
  curl -fsS http://127.0.0.1:8000/meter
  ```

## RPi Zero Docker Cross‑Build & Test

You can verify that the codebase builds and tests successfully on an
RPi Zero–compatible arm/v6 rootfs using Docker buildx and QEMU
emulation. This does not exercise real hardware but provides strong
compatibility assurance.

- Prerequisites: Docker (with buildx) installed on your host.

Steps:

1. Initialize the cross‑build environment (installs QEMU emulators and selects a builder):

   ```bash
   ./scripts/buildx_setup.sh
   ```

2. Cross‑build and run tests for linux/arm/v6:

   ```bash
   ./scripts/rpi0_build_test.sh
   ```

   This uses `docker/Dockerfile.rpi0` and executes `pytest tests` inside
   the image under QEMU. If tests pass, the image `eco-rpi0:test` is
   loaded into your local Docker.

3. Build a runtime image for RPi Zero (arm/v6):

   ```bash
   ./scripts/rpi0_build_runtime.sh
   ```

4. Run the FastAPI simulation API locally (useful for quick sanity checks):

   ```bash
   ./scripts/rpi0_run_app.sh
   # Then visit http://localhost:8000/docs
   ```

Notes:

- The Docker base image is `balenalib/raspberry-pi-python:3.9-bullseye`,
  which targets ARMv6 hard‑float (RPi Zero/Zero W).
- The test stage only runs top‑level tests under `tests/` (not submodule tests).
- For full hardware integration (e.g., tuntap, PLC drivers), run on a real RPi with `setup_rpi.sh`.

## CCS DC Charging Simulation Suite

The repository ships with a self‑contained simulation environment for
exercising a complete CCS DC charging session.  The suite emulates the
control pilot (CP) signal, pre‑charge ramp and a basic energy meter so
that the high‑level ISO 15118 logic can be tested without real power
hardware.

### Modules

The simulation lives under `src/ccs_sim` and is composed of:

* `pwm.py` – generates a 1 kHz CP PWM signal and reports CP voltage
  levels.  In the absence of GPIO/ADC hardware it returns simulated
  values.
* `precharge.py` – models a simple DC power supply and a pre‑charge
  controller that ramps the voltage to match the EV battery while
  limiting inrush current.
* `emeter.py` – integrates voltage and current readings to provide
  session energy statistics.
* `orchestrator.py` – coordinates the complete charging sequence from
  vehicle plug‑in through charging and session termination.
* `fastapi_app.py` – optional FastAPI wrapper exposing `/start_session`
  and `/status` endpoints for remote triggering and monitoring.

### Running the simulation

Run the orchestrator directly to exercise the full flow:

```bash
python src/ccs_sim/orchestrator.py
```

The script waits for the CP to transition from state A to state B.  In
simulation mode this can be triggered from another Python shell by
calling `pwm.simulate_cp_state("B")`.  The orchestrator then performs
the cable check, pre‑charge and a short charging loop while reporting
voltage, current and accumulated energy.

To drive the session via an HTTP API, launch the FastAPI application:

```bash
uvicorn ccs_sim.fastapi_app:app --host 0.0.0.0 --port 8000
```

POST to `/start_session` to begin a sequence and query `/status` for
live metrics.

To confirm runtime dependencies, invoke the main program with `--help` and
ensure it prints usage information:

```bash
python src/evse_main.py --help
```

## Contributing

Contributions are welcome! Please read the [contributing guidelines](CONTRIBUTING.md) for more information.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more information.
## Hardware Adapters

The EVSE HAL supports pluggable backends:

- sim: All functions simulated in-process (default).
- esp-uart: Uses an ESP32-S3 CP helper over UART for Control Pilot (PWM + ADC). Other devices remain simulated.
- esp-periph: Adds a JSON-RPC peripheral coprocessor (ESP32-S3) over UART for contactor, temperature, and meter while keeping CP/PWM via the existing ESP CP helper (if available) or sim fallback.

Select an adapter at runtime:

```
export EVSE_CONTROLLER=hal
export EVSE_HAL_ADAPTER=esp-periph   # or esp-uart or sim
# CP UART for esp-uart or esp-periph with CP support
export ESP_CP_PORT=/dev/serial0
# Peripheral coprocessor UART for esp-periph
export ESP_PERIPH_PORT=/dev/ttyUSB0
python3 start_evse.py --iface eth0 --controller hal
```

### ESP Peripheral Coprocessor (JSON-RPC)

The peripheral offloads GPIO-heavy, time-critical operations (contactor coil with aux prove-out, gun temperatures, meter sampling). It exposes a small JSON-RPC API over a newline-delimited UART.

- Keepalive: The Pi sends `sys.ping` periodically (client defaults to 1.5 s). ESP fails safe to contactor OFF on missed keepalives.
- Arming: `contactor.set` requires a preceding `sys.arm` within ~1.5 s. The client auto-arms.
- Simulation Mode: Switch via `sys.set_mode` without changing the Pi logic.

Quick demo without the full EVSE stack:

```
python3 scripts/esp_periph_demo.py --port /dev/ttyUSB0
```
