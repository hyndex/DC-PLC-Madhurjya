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

These controls do not change protocol semantics and are safe defaults. Increase/decrease per site as needed based on PLC link quality.

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
