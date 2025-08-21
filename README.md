# DC Single GUN PLC for Raspberry Pi

This project provides a Python-based implementation of the ISO 15118 and SLAC protocols for a single-gun DC charger, designed to run on a Raspberry Pi.
> **Note:** This repository uses Git submodules. After cloning, run `git submodule update --init --recursive` to download them.

## Features

*   **ISO 15118-2 and ISO 15118-20 compliant:** Supports both AC and DC charging, as well as Plug & Charge (PnC).
*   **SLAC protocol support:** Implements the SLAC protocol for establishing a communication link between the EV and EVSE.
*   **Modular and extensible:** The project is designed to be easily extended and customized for different hardware and use cases.
*   **Raspberry Pi compatible:** The project is optimized for running on a Raspberry Pi, making it a cost-effective solution for EVSE development.

## Architectural Overview

`src/evse_main.py` orchestrates the charger logic. It links the QCA7000 PLC modem
with a Linux TAP interface, invokes [`pyslac`](src/pyslac) to perform Signal Level
Attenuation Characterization (SLAC) and, once a vehicle is matched, hands the
session to the ISO 15118 stack provided by the [`iso15118`](https://pypi.org/project/iso15118/)
Python package. Each component is replaceable, enabling custom hardware front‑ends or
SECC implementations.

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
script that enables SPI, installs dependencies and configures the QCA7000
overlay.

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

1. **Setup script** – [`setup_rpi.sh`](setup_rpi.sh) enables SPI, installs
   dependencies and configures the QCA7000 overlay.
2. **PLC↔TAP bridge** – `evse_main.py` connects the modem to a Linux TAP
   interface so higher layers can communicate over IPv6.
3. **SLAC** – [`pyslac`](src/pyslac) matches the vehicle and establishes a
   powerline link.
4. **ISO 15118 session** – once matched, the SECC from the `iso15118` package
   negotiates charging parameters with the EV.

## Usage

The `evse_main.py` helper in `src/` bridges the PLC modem to a TAP
interface, performs SLAC matching using `pyslac` and, once matched,
launches the ISO 15118 SECC bound to the same interface.

```
python src/evse_main.py --evse-id <EVSE_ID> \
    --slac-config path/to/pyslac.env \
    --secc-config path/to/secc.env \
    --cert-store pki
```

* `--slac-config` – optional path to a PySLAC `.env` file
* `--secc-config` – optional path to an ISO 15118 SECC `.env` file
* `--cert-store` – directory containing ISO 15118 certificates (`PKI_PATH`), defaults to `pki`

The TAP interface defaults to `192.168.1.1/24` but can be changed via
`--iface-ip` and `--iface-netmask`.

## Configuration and Certificates

Environment variables drive both `pyslac` and the ISO 15118 SECC. Create
two `.env` files and point `evse_main.py` at them with the
`--slac-config` and `--secc-config` flags:

```bash
# pyslac.env
QCA7000_IFACE=spi0.0

# secc.env
IFACE=tap0
EVSE_ID=DE*PNC*E12345*1
```

Additional options are documented in the respective packages. Certificates
for Plug & Charge are generated with
[`scripts/generate_certs.sh`](scripts/generate_certs.sh) and stored under
`pki/` by default.

## Troubleshooting and Hardware Notes

* Confirm SPI wiring (MOSI/MISO/SCK/CS/INT) between the Raspberry Pi and
  the QCA7000 modem is short and correctly oriented. Detailed pin‑outs are
  available in [docs/STAMPMICRO.md](docs/STAMPMICRO.md).
* After running the setup script, `ip link` should list the PLC interface
  (e.g. `plc0`). Update `.env` files if the interface name differs.
* Flow and wiring diagrams plus additional tips live in
  [docs/plug_and_play.md](docs/plug_and_play.md).

## Testing and Verification

Run the unit tests with [pytest](https://pytest.org/) to verify the
installation:

```bash
pip install -r requirements.txt
pytest
```

To confirm runtime dependencies, invoke the main program with `--help` and
ensure it prints usage information:

```bash
python src/evse_main.py --help
```

## Contributing

Contributions are welcome! Please read the [contributing guidelines](CONTRIBUTING.md) for more information.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more information.
