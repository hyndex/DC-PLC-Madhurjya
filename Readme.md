# DC Single GUN PLC for Raspberry Pi

This project provides a Python-based implementation of the ISO 15118 and SLAC protocols for a single-gun DC charger, designed to run on a Raspberry Pi.
> **Note:** This repository uses Git submodules. After cloning, run `git submodule update --init --recursive` to download them.

**Note:** This repository uses Git submodules for ISO 15118 and SLAC implementations. After cloning, run `git submodule update --init --recursive` to populate `src/iso15118` and `src/pyslac`.

## Features

*   **ISO 15118-2 and ISO 15118-20 compliant:** Supports both AC and DC charging, as well as Plug & Charge (PnC).
*   **SLAC protocol support:** Implements the SLAC protocol for establishing a communication link between the EV and EVSE.
*   **Modular and extensible:** The project is designed to be easily extended and customized for different hardware and use cases.
*   **Raspberry Pi compatible:** The project is optimized for running on a Raspberry Pi, making it a cost-effective solution for EVSE development.

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
pip install -r requirements.txt
pip install -e src/pyslac src/iso15118
```

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
sudo python3 start_evse.py
```

Troubleshooting tips and a flow diagram of the process are available in
[docs/plug_and_play.md](docs/plug_and_play.md).

### Usage

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

## Contributing

Contributions are welcome! Please read the [contributing guidelines](CONTRIBUTING.md) for more information.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more information.
