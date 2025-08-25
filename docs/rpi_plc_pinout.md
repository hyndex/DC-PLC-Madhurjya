# PLC Stamp micro 2 ↔ Raspberry Pi Zero 2 W SPI Wiring

This document summarises how to connect the PLC Stamp micro 2 module to a
Raspberry Pi Zero 2 W using the SPI host interface. The information is
derived from the module datasheet and the QCA7000 SPI application note
([PLC Stamp micro 2 datasheet](https://chargebyte.com/assets/downloads/datasheet_plcstampmicro2-rev14-1.pdf),
[Application Note AN4](https://chargebyte.com/assets/downloads/an4_rev5.pdf)).

## Required signals

| PLC Stamp micro 2 pin | Function                          | Raspberry Pi pin (BCM) |
|-----------------------|-----------------------------------|------------------------|
| 1                     | VDD (3.3 V)                       | 1 (3V3)                |
| 7–9, 11–17, 28        | GND                               | any ground             |
| 26                    | SERIAL_1 – SPI CLK (CPOL=1,CPHA=1)| 23 (GPIO11 SCLK)       |
| 23                    | SERIAL_4 – SPI MOSI               | 19 (GPIO10 MOSI)       |
| 24                    | SERIAL_3 – SPI MISO               | 21 (GPIO9 MISO)        |
| 25                    | SERIAL_2 – SPI CS (active low)    | 24 (GPIO8 CE0)         |
| 27                    | SERIAL_0 – IRQ to host            | 22 (GPIO25)            |
| 22                    | RESET_L (active low)              | 18 (GPIO24, pull‑up)   |
| 6                     | ZC_IN (tie to GND for pilot use)  | GND                    |
| 2–5                   | TX/RX P/N (to coupling network)   | —                      |

Notes:

* The module uses **SPI mode 3** and supports clock speeds up to
  12 MHz. Keep chip‑select low for the entire SPI transaction.
* RESET_L should be pulled high (10 kΩ to 3V3) and may be driven low by
  the Pi to reset the modem.
* The interrupt line on SERIAL_0 asserts high; configure the kernel
  overlay with the chosen GPIO (`int_pin`).
* TX/RX pins connect to the required isolation/coupling network and **do
  not** go to the Pi directly.
* For automotive pilot/dead‑wire applications tie ZC_IN to ground.

With the wiring above, the Raspberry Pi kernel driver `qcaspi` can be
enabled via the `qca7000` device‑tree overlay and the modem will appear
as a regular Ethernet interface (e.g. `eth1`).
