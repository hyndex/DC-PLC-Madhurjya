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

On Raspberry Pi, set `ESP_CP_PORT` (e.g., `/dev/serial0` or `/dev/ttyAMA0`) and ensure 115200 8N1. If unset, the client defaults to `/dev/serial0`. Example:

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
- Use `GET /cp` to observe CP state/voltage and `/status` for session state; `/control/pwm` affects only sim or manual firmware mode.

Logging
- Configure unified logs with env vars:
  - `EVSE_LOG_LEVEL=DEBUG|INFO|...` (default INFO)
  - `EVSE_LOG_FORMAT=text|json` (default text)
  - `EVSE_LOG_FILE=/path/to/file.log` (optional)
- UART client logs TX/RX lines at DEBUG under logger `esp.cp`.
- Orchestrator emits event/periodic logs under `orchestrator`; precharge under `precharge`; API under `api`.

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
