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
./scripts/generate_certs.sh
sudo python3 src/evse_main.py --evse-id <EVSE_ID>
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

## Testing and Verification

Run the unit tests with [pytest](https://pytest.org/) to verify the
installation:

```bash
pip install -r requirements.txt
pytest
```

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
