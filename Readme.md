# DC Single GUN PLC for Raspberry Pi

This project provides a Python-based implementation of the ISO 15118 and SLAC protocols for a single-gun DC charger, designed to run on a Raspberry Pi.

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
```

2.  Install the dependencies:

```
pip install -r requirements.txt
```

### Usage

To start the EVSE, run the following command:

```
python -m pyslac.examples.ev_slac_scapy
```

## Contributing

Contributions are welcome! Please read the [contributing guidelines](CONTRIBUTING.md) for more information.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more information.