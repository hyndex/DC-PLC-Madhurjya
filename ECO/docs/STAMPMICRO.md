# PLC Stamp micro 2 Datasheet (Rev. 14, Oct 31, 2023)

## 1. Revisions

| Revision | Release Date       | Changes                                                                                                                                                                          |
| -------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **14**   | October 31, 2023   | Updated Corporate Identity; updated module image; added part number for alternative 1:1:1 transformer to Available Accessories.                                                  |
| **13**   | May 23, 2022       | Format/layout changes; graphics quality improved.                                                                                                                                |
| **12**   | March 04, 2022     | Updated Corporate Identity; changed QCA7000 as default; changed order list in section 13; added new section 15 "Table of non-standard versions"; added new section 16 "Contact". |
| **11**   | January 27, 2021   | Format/layout changes.                                                                                                                                                           |
| **10**   | May 05, 2020       | Added versions -003 and -004 to order code table; added "Motorola" to SPI mode description; updated example module label; added table with QCA firmware versions.                |
| **9**    | September 05, 2017 | Added section "Processing".                                                                                                                                                      |
| **8**    | June 13, 2017      | Updated section "GPIO"; corrected error in meaning of GPIO levels.                                                                                                               |
| **7**    | March 09, 2017     | Added hints for use in PEVs in section "Getting Started".                                                                                                                        |
| **6**    | February 01, 2017  | Added package materials information and order options; added info about differences in QCA7000 vs QCA7000.                                                                       |
| **5**    | March 21, 2016     | Added GPIO output current limit, all GPIO3 functions and timings.                                                                                                                |
| **4**    | February 24, 2016  | Fixed GPIO function assignment.                                                                                                                                                  |
| **3**    | February 09, 2016  | Clarified UART settings.                                                                                                                                                         |
| **2**    | January 25, 2016   | Added default UART settings.                                                                                                                                                     |
| **1**    | November 16, 2015  | Initial release.                                                                                                                                                                 |

## 2. Abstract

The PLC (PowerLine Communication) **Stamp Micro 2** module gives your application access to powerline communication based on the HomePlug® Green PHY™ chipset QCA7000. You can realize point-to-point and multi-point connections depending on your application. Data is transmitted as Ethernet packets over the powerline, allowing the use of TCP/IP or any network protocols of your choice.

You can freely select the galvanic isolation from the powerline and the power supply so that it perfectly meets the requirements for your application. The QCA7000 by Qualcomm Atheros ensures compatibility with many other commercial powerline devices.

**Key Parameters:** *(See Table below)*

| **Parameter**          | **Value**                                                   |
| ---------------------- | ----------------------------------------------------------- |
| **Power supply**       | 3.3 V                                                       |
| **Power consumption**  | 0.5 W                                                       |
| **Data rate**          | max. 10 MBit/s                                              |
| **Reach**              | max. 300 m via powerline                                    |
| **Temperature range**  | –40 °C to +85 °C (industrial) / 0 °C to +70 °C (commercial) |
| **Outline dimensions** | 22 mm × 22 mm × 4.5 mm                                      |
| **Weight**             | 3.3 g                                                       |
| **RoHS**               | PLC Stamp micro 2 is manufactured in compliance with RoHS   |

> **Note:** The default configuration now uses the **QCA7000** chipset. Earlier revisions with a QCA7005 package remain pin‑compatible but are no longer recommended for new designs.

## 3. Applications

* Interconnecting household appliances to the Smart Grid
* Connecting smart meters to Smart Meter Gateways and/or LAN/WAN/WiFi
* Connecting sensors and photovoltaic equipment
* Connecting heating and air conditioning systems
* Coupling machines and measurement devices
* Forwarding digital signals (remote I/O)
* Coupling of RF cells for home automation

## 4. Interfaces

* **Powerline Interface:** Supports coupling to 230 V AC, 110 V AC, DC lines, or dead-wire 2-wire connections. External coupling circuits are required for different mediums (mains or pilot wire).
* **Serial Interface:** UART or SPI (selectable as an order option) for host communication with the module.

## 5. Handling

This module is an ESD-sensitive electronic component. It has components with moisture sensitivity level (MSL) 3 – appropriate handling and storage precautions must be observed. Use standard ESD protective measures when handling the module, and follow MSL level 3 device guidelines (e.g., controlled environment after package opening, limited floor life before reflow, etc.).

## 6. Module Overview

The block diagram in **Figure 1** illustrates the module components (gray box) and the required external connections and components. Inside the module, the **QCA7000** HomePlug Green PHY IC is accompanied by a flash memory (for firmware/PIB storage). External connections include the powerline coupling interface (TX/RX lines and a zero-cross detection input), a 1→3.3 V power regulator (to supply VDD 3.3 V to the module), and the host interface signals (SPI or UART plus GPIO lines). The external **coupling network** (not part of the module) typically contains isolation transformers and capacitors to connect the TX/RX signals to the power line or pilot wire, as well as a **zero-cross detector** circuit for synchronization (e.g., via an optocoupler to mains in EV charging applications).

*Figure 1 – Block Diagram of PLC Stamp micro 2.* (The module is represented by the gray boxed area. It shows the QCA7000 chip and its SPI Flash inside the module. Connections to the outside include the **power supply** (3.3 V input), **powerline interface** (TX\_P/N, RX\_P/N pins connecting to an external coupling transformer and line interface, plus a ZC\_IN input from an external zero-cross detection circuit), and the **serial host interface** (SPI or UART signals) along with four GPIOs.)

**Figure 2** shows an image of the module. All electronic components are mounted on the PCB and covered by a metal RF shield can. A thermal label on top of the shield provides module information (label details are described in Section 12).

*Figure 2 – Image of the PLC Stamp micro 2 module.* (The module PCB is 22 × 22 mm. Most components are beneath a metal shielding can. The label on the shield typically includes the order code, MAC address, serial number, etc. A dot or mark on the label indicates Pin 1 location.)

## 7. Technical Data

### 7.1 Absolute Maximum Ratings

*Table 1 – Absolute Maximum Ratings.*

| **Symbol**     | **Parameter**                          | **Min.** | **Max.** | **Unit** |
| -------------- | -------------------------------------- | -------- | -------- | -------- |
| VDD            | Digital supply voltage                 | –0.30    | +3.46    | V        |
| VDIO           | Digital input voltage                  | –0.30    | +3.63    | V        |
| TSTORE         | Storage temperature                    | –40      | +150     | °C       |
| R<sub>AH</sub> | Relative air humidity (non-condensing) | 10       | 90       | %        |

### 7.2 Operating Conditions

*Table 2 – Operating Conditions.*

| **Symbol**       | **Parameter**                   | **Min.** | **Typ.**      | **Max.** | **Unit** |
| ---------------- | ------------------------------- | -------- | ------------- | -------- | -------- |
| VDD              | Digital supply voltage          | +3.13    | +3.30         | +3.46    | V        |
| I<sub>DD</sub>   | Supply current (VDD)            | –        | 150 (average) | 300      | mA       |
| T<sub>CASE</sub> | Max. ambient temp. (industrial) | –40      | –             | +85      | °C       |
| *\[same]*        | Max. ambient temp. (commercial) | 0        | –             | +70      | °C       |
| I<sub>GPIO</sub> | GPIO pin source/sink current    | –        | –             | 12       | mA       |

**Temperature ranges:** Industrial: –40 to +85 °C; Commercial: 0 to +70 °C. Exceeding absolute maximum ratings may damage the module. Recommended operating conditions should be maintained for reliable operation.

## 8. Firmware and MAC Addresses

Each PLC Stamp micro 2 module comes pre-programmed with firmware and a Parameter Information Block (PIB) specific to the module. The PIB contains the module’s MAC addresses, output power calibration (prescaler values), and settings for automotive usage (SLAC parameters for EV/EVSE). The MAC address uses an Organizationally Unique Identifier (OUI) assigned to **chargebyte GmbH**, ensuring a unique address space for these devices. The prescaler values (transmit gain settings) are set during production as defined by Qualcomm for the intended application/market (see the **Parameter Optimization** code in the order information). Automotive variants have SLAC (Signal Level Attenuation Characterization) enabled in firmware for either the EVSE or the PEV side as appropriate.

## 9. Module Pinout

The module has 28 pins. Table 3 below lists the pin assignments, their functions, and directions.

*Table 3 – PLC Stamp micro 2 Module Pinout.*

| **Pin** | **Dir** | **Name**  | **Description**                            |
| ------: | :-----: | --------- | ------------------------------------------ |
|       1 |  SUPPLY | VDD       | +3.3 V Supply voltage for the module       |
|       2 |    IN   | RX\_N     | Powerline receiver input (negative)        |
|       3 |    IN   | RX\_P     | Powerline receiver input (positive)        |
|       4 |   OUT   | TX\_N     | Powerline transmitter output (negative)    |
|       5 |   OUT   | TX\_P     | Powerline transmitter output (positive)    |
|       6 |    IN   | ZC\_IN    | Zero-cross detection input                 |
|       7 |  SUPPLY | GND       | Ground                                     |
|       8 |  SUPPLY | GND       | Ground                                     |
|       9 |  SUPPLY | GND       | Ground                                     |
|      10 |    –    | –         | *Not available (mechanical key/alignment)* |
|      11 |  SUPPLY | GND       | Ground                                     |
|      12 |  SUPPLY | GND       | Ground                                     |
|      13 |  SUPPLY | GND       | Ground                                     |
|      14 |  SUPPLY | GND       | Ground                                     |
|      15 |  SUPPLY | GND       | Ground                                     |
|      16 |  SUPPLY | GND       | Ground                                     |
|      17 |  SUPPLY | GND       | Ground                                     |
|      18 |   I/O   | GPIO\_0   | QCA7000 GPIO 0                             |
|      19 |   I/O   | GPIO\_1   | QCA7000 GPIO 1                             |
|      20 |   I/O   | GPIO\_2   | QCA7000 GPIO 2                             |
|      21 |   I/O   | GPIO\_3   | QCA7000 GPIO 3                             |
|      22 |    IN   | RESET\_L  | Reset input (active low)                   |
|      23 |   I/O   | SERIAL\_4 | QCA7000 serial interface signal 4          |
|      24 |   I/O   | SERIAL\_3 | QCA7000 serial interface signal 3          |
|      25 |   I/O   | SERIAL\_2 | QCA7000 serial interface signal 2          |
|      26 |   I/O   | SERIAL\_1 | QCA7000 serial interface signal 1          |
|      27 |   I/O   | SERIAL\_0 | QCA7000 serial interface signal 0          |
|      28 |  SUPPLY | GND       | Ground                                     |

**Pin directions:** *SUPPLY* pins provide power or ground. *IN* pins are inputs to the module. *OUT* pins are outputs from the module. *I/O* pins are bidirectional or configurable.

### 9.1 GPIO

#### 9.1.1 Power-on Configuration (Bootstrap)

The QCA7000 uses four GPIO pins for bootstrap configuration. These GPIOs are read during boot to determine the module’s startup configuration. **Table 4** shows the QCA7000 bootstrap options and the default strap settings on the module.

*Table 4 – QCA7000 Boot Strap Options.*

| **GPIO #** | **Boot Configuration Function** | **Pull-Up Option** | **Pull-Down Option** | **Module Default (Preload)**  |
| ---------: | ------------------------------- | ------------------ | -------------------- | ----------------------------- |
|      **0** | Boot Source                     | Flash (internal)   | Host (external)      | – (no resistor on module)     |
|      **1** | Host Interface Select           | – (no pull-up)     | SPI Slave mode       | 10 kΩ Pull-Down (selects SPI) |
|      **2** | SPI Mode Select                 | Burst mode (new)   | Legacy mode (old)    | – (no resistor on module)     |
|      **3** | – *(No boot function)*          | –                  | –                    | – (no resistor on module)     |

(GPIO0–GPIO2 each have an internal pull-up/down that selects a configuration on boot; the module’s design preloads only GPIO1 with a pull-down to choose SPI by default.)

#### 9.1.2 GPIO Functions (After Boot)

After booting, the GPIO pins are used by the QCA7000 firmware for various status indications and control inputs. **These GPIOs are not available for user application control** – they are managed solely by the QCA7000 firmware. Table 5 summarizes the default behavior of each GPIO after boot.

*Table 5 – QCA7000 GPIO Settings (Default Firmware Usage).*

| **GPIO #** | **Direction** | **Function (default firmware)**                                                                                                                                                            |
| ---------: | :-----------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
|      **0** |     Output    | PLC connection status – **1** when PLC link is established; **0** when no connection.                                                                                                      |
|      **1** |     Output    | Pushbutton Simple Connect status – toggles 1/0 at 1 Hz when Simple Connect mode is active; **0** when not in Simple Connect mode.                                                          |
|      **2** |     Output    | *Unused* (no function in default firmware)                                                                                                                                                 |
|      **3** |     Input     | Pushbutton Simple Connect trigger – press and hold to initiate functions: 0.5–3 s = enter Simple Connect mode; 5–8 s = generate new NMK (network key); 10–15 s = restore factory defaults. |

Since the GPIOs double as bootstrap pins, special attention is required when connecting LEDs or switches to them. **Figure 3** and **Figure 4** illustrate the recommended LED and push-button connections for GPIO straps. The wiring must respect the pull-up or pull-down strap so that the intended logic levels are achieved without conflict. Table 5 defines a logical "1" in terms of user interaction (LED lit or button pressed), but the actual electrical high/low level depends on the strap resistor orientation (pull-up vs pull-down).

*Figure 3 – GPIO LED Bootstrap Wiring.* (This figure shows a GPIO output driving an LED. The LED and its series resistor are connected such that the LED lights when the GPIO outputs a "1" state. The diagram indicates the placement of the bootstrap resistor and the LED relative to VDD and GND, ensuring that the correct default level is maintained during boot.)

*Figure 4 – GPIO Switch Bootstrap Wiring.* (This figure shows a momentary push-button connected to a GPIO input used as a strap. A resistor provides the default pull (either to VDD or GND) and the switch connects the line to the opposite rail when pressed. The illustration ensures that pressing the button corresponds to a logical "1" as defined in Table 5, given the default strap pull direction.)

### 9.2 Serial Signals

The signals **SERIAL\_0** through **SERIAL\_4** correspond to the QCA7000’s serial interface lines. Depending on the firmware variant, these signals serve either **SPI** or **UART** functions. Table 6 shows the mapping of each signal in SPI mode vs. UART mode.

*Table 6 – QCA7000 UART/SPI Signal Mapping.*

| **Signal Name** | **SPI Function** | **UART Function**         |
| --------------- | ---------------- | ------------------------- |
| **SERIAL\_0**   | Interrupt (IRQ)  | *(not used in UART mode)* |
| **SERIAL\_1**   | CLK              | RTS (Ready to Send)       |
| **SERIAL\_2**   | CS (Chip Select) | CTS (Clear to Send)       |
| **SERIAL\_3**   | MISO             | TXD (Transmit Data)       |
| **SERIAL\_4**   | MOSI             | RXD (Receive Data)        |

> **Note:** The selection of SPI vs. UART mode is determined by the module’s firmware. It is an **order option** – i.e., different firmware builds are provided for SPI or UART interface variants. The default (standard) module variants use SPI firmware.

#### 9.2.1 SPI Mode

When using the SPI interface, the QCA7000 operates in **Motorola SPI Mode 3** (CPOL=1, CPHA=1). The SPI should be operated in **burst mode**, meaning the chip select (CS) line is held low for the entire duration of each SPI message (do not toggle CS between bytes). The SPI clock period must be ≥ 83.3 ns (which corresponds to a maximum SPI clock frequency of \~12 MHz).

#### 9.2.2 UART Mode

For UART interface variants, the modules use the following default UART settings (Table 7). Note that the RTS/CTS flow control lines are not used in the QCA7000’s UART implementation.

*Table 7 – UART Default Settings.*

|  **Setting** | **Value**         |
| -----------: | :---------------- |
|    Baud Rate | 115200 bps        |
|    Data Bits | 8                 |
|       Parity | None              |
|    Stop Bits | 1                 |
| Flow Control | None (no RTS/CTS) |

### 9.3 Recommended Footprint

**Figure 5** illustrates the recommended PCB footprint (pad layout) for the PLC Stamp micro 2 module. The drawing is a **top view** of the footprint (looking “through” the module from above). All pad dimensions are in millimeters, and all pads are of equal size and evenly spaced except where noted. Areas designated as “restricted” (keep-out areas under the module) should be kept free of copper on the host PCB. The module outline in the figure shows ideal nominal dimensions (tolerances not included).

*Figure 5 – PLC Stamp micro 2 Footprint (Recommended PCB Land Pattern).* **Notes:** 1) All dimensions in mm. 2) All pads are the same size. 3) Pad pitch is uniform unless otherwise specified. 4) Figure 5 shows the footprint as viewed from the top (through the module). 5) Restricted areas (under module) should have no copper on the PCB. 6) Module outline dimensions shown are nominal (no tolerance included).

## 10. Getting Started

Before integrating the PLC Stamp micro 2, there are several design decisions to consider:

1. **Choice of Serial Interface (SPI vs UART):** *SPI* is more robust (synchronous, less susceptible to noise) and supports higher throughput (max clock \~12 MHz), but is more complex to implement. *UART* is simpler to use but slower (max baud rate, e.g., 115.2 kbps) and less robust over noise. The default module variant uses **SPI**, and SPI is generally recommended for best performance. UART variants are available if simplicity is prioritized over speed.

2. **Powerline Coupling Medium:** Decide what type of line the module will communicate over. If coupling to a **clean low-voltage transmission line** (e.g., a dedicated twisted pair or pilot wire), isolation and transient suppression requirements are simpler. If coupling to **mains power (e.g., CAT IV 3-phase 230 V)**, robust isolation (high-voltage transformers or couplers) and transient protection (surge suppressors, MOVs, safety capacitors) are required. The design of the coupling network will differ for mains vs. pilot wire.

3. **Noise/Loading on the Line:** Consider if there are noise sources or significant capacitive loads on the communication line. Noise in the 2–30 MHz band (the PLC frequency range) can degrade communication. For example, switching power supplies or solar inverters might inject noise, and large line-to-line capacitors (e.g., across L and N in some equipment) can absorb PLC signals (effectively shorting out the high-frequency communication). These issues should be mitigated (use filters, avoid large RF shunts, etc.).

A reference coupling circuit is provided for guidance: **Figure 6** shows an example coupling network for PLC on mains (AC line), and **Figure 7** shows a coupling network for an automotive pilot line (DC dead-wire). These schematics are based on Qualcomm Atheros’s QCA7000 Add-In Reference Design.

*Figure 6 – Reference Schematic for PLC Coupling to Mains.* (This schematic illustrates an isolation transformer-based coupling for AC mains. It typically includes a high-frequency coupling transformer with a ratio such as 1:4:5, line coupling capacitors rated for mains voltage (denoted C1, C2, etc.), a transient voltage suppressor, and an optocoupler for zero-cross detection on the AC line. **Note:** Capacitors C1, C2, C5, C6 in the schematic must be chosen for the environment – they will see near full line voltage. Use safety-rated X capacitors for mains coupling. Ensure all applicable product safety requirements (insulation, creepage, surge protection) are met.)

*Figure 7 – Reference Schematic for PLC Coupling to Pilot Signal (Automotive).* (This schematic shows coupling to a pilot or dead-wire (e.g., EV charging pilot line). It uses a 1:1:1 coupling transformer and lower voltage capacitors since the pilot is typically a low-voltage signal. If the pilot might cross-couple to other mains-powered lines, a zero-cross detector circuit (optocoupler) is included tied into the mains reference. For purely isolated pilot-wire communication (e.g., in a PEV), the ZC\_IN signal of the module should be tied to GND and the optocoupler circuitry can be omitted. Automotive variants include preset SLAC settings as noted.)

Chargebyte provides tested **accessory components** (such as the recommended coupling transformers) to implement these designs in your application. See section **13.1 Available Accessories** for references to these parts.

For rapid prototyping, an **evaluation kit** is available. Using the PLC Stamp micro 2 with chargebyte’s eval kit is the easiest way to get started – contact your distributor or chargebyte GmbH for details.

## 11. Processing (Soldering & Assembly)

* Process these modules according to IPC/JEDEC **J-STD-020** (Moisture/Reflow Sensitivity) and **J-STD-033** (Handling of Moisture/Reflow Sensitive Devices) guidelines. In practice, this means baking the modules if required and controlling exposure times once dry-pack is opened.
* Limit the module to a maximum of **2 reflow soldering** cycles. (At most one reflow for initial assembly and one reflow for rework; more can damage the module or components.)

## 12. Module Marking

Each module is marked with a durable thermal label on the metal shield. The label contains the following data:

1. **Order Code** – The full module order number (e.g., `I2PLCBMC-ISP-004-R`).
2. **QCA7000 MAC Address** – Printed in hexadecimal with a colon every two digits (e.g., `00:01:87:xx:xx:xx`). *(Note: the 2D barcode on the label encodes the MAC address as a continuous string without separators.)*
3. **Serial Number** – A unique serial identifier for the module.
4. **Production Date Code** – In WWYY format (week-year).
5. **Device Security Key** – A module-specific security key (alphanumeric, for firmware or network use if applicable).
6. **Data Matrix Code** – A 2D data matrix barcode encoding all the above information as a space-separated string.

Additionally, each label features the **chargebyte logo/banner** and a **Pin 1 marking** (to help identify the orientation of the module).

*Figure 8 – Example Label for PLC Stamp micro 2.* (The figure shows a sample label layout: the chargebyte logo at top, a filled circle or similar symbol marking Pin 1, and text lines for Order Code, MAC address, Serial No., Date Code, Device Key. A data matrix code is printed as well. The Pin 1 mark on the label aligns with Pin 1 on the module PCB for orientation.)

## 13. Order Information

The order code for PLC Stamp micro 2 modules is composed of several parts, encoding the chip type, temperature range, interface, and other options. **Table 8** explains the structure of the order code.

*Table 8 – PLC Stamp micro 2 Order Code Composition.*

| **Field**                  | **Code**                                                                                                                                                | **Description**                                                                                                         |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Product Family**         | `I2PLC`                                                                                                                                                 | PLC Stamp Micro 2 module family                                                                                         |
| **Chip**                   | QCA7000 | PLC chipset used in all standard modules |
| **Variant** (Temperature)  | `I` = Industrial –40 to +85 °C   <br>`C` = Commercial 0 to +70 °C\* (for QCA7000 only)                                                                  | Operating temperature range <br>\*(*Commercial temp available only on QCA7000 legacy modules*)                          |
| **Serial Interface**       | `S` = SPI (default)              <br>`U` = UART (on request)                                                                                            | Host interface mode (requires corresponding firmware)                                                                   |
| **Parameter Optimization** | `E` = Automotive EVSE (Charging Station) <br>`P` = Automotive PEV (Vehicle) <br>`C` = CE Class B (EU compliance) <br>`N` = North America (FCC bandplan) | Pre-optimized settings in firmware for specific use-case or region (affecting PLC signal profile, bandplan, SLAC, etc.) |
| **Firmware Version**       | `-004` = QCA firmware v1.2.5 (current) <br>`-002` = QCA firmware v1.1.3\* (legacy)                                                                      | Qualcomm firmware/PIB version loaded <br>\*(*v1.1.3 is older and not recommended for new designs*)                      |
| **Packaging**              | `-T` = Tray (40 pcs per tray)    <br>`-R` = Tape & Reel (370 pcs per reel)                                                                              | Packaging format for shipment                                                                                           |

**Example:** Order code **`I2PLCBMC-ISP-004-R`** corresponds to: *I2PLC* family, *B* (QCA7000), *M* (module subtype, “Micro” series), *C* (Industrial temp variant), then *-ISP-*: SPI interface, Automotive PEV optimized, firmware 1.2.5, *-R* reel packaging.

The table below lists the **standard available variants** of PLC Stamp micro 2 (using QCA7000, firmware 1.2.5). Other combinations (including UART interface or QCA7000-based modules) are **non-standard** and provided only on request (see section 15).

*Table 9 – Standard PLC Stamp micro 2 Order Codes (QCA7000, SPI, FW 1.2.5).*

| **Order Code**         | **Chip** | **Temp. Range** | **Serial IF** | **Param. Opt.** | **Packaging**         |
| ---------------------- | -------- | --------------- | ------------- | --------------- | --------------------- |
| **I2PLCBMC-ISE-004-T** | QCA7000  | –40 to +85 °C   | SPI           | Automotive EVSE | Tray (40 pcs)         |
| **I2PLCBMC-ISE-004-R** | QCA7000  | –40 to +85 °C   | SPI           | Automotive EVSE | Tape & Reel (370 pcs) |
| **I2PLCBMC-ISP-004-T** | QCA7000  | –40 to +85 °C   | SPI           | Automotive PEV  | Tray (40 pcs)         |
| **I2PLCBMC-ISP-004-R** | QCA7000  | –40 to +85 °C   | SPI           | Automotive PEV  | Tape & Reel (370 pcs) |
| **I2PLCBMC-ISC-004-T** | QCA7000  | –40 to +85 °C   | SPI           | CE Class B      | Tray (40 pcs)         |
| **I2PLCBMC-ISC-004-R** | QCA7000  | –40 to +85 °C   | SPI           | CE Class B      | Tape & Reel (370 pcs) |
| **I2PLCBMC-ISN-004-T** | QCA7000  | –40 to +85 °C   | SPI           | North America   | Tray (40 pcs)         |
| **I2PLCBMC-ISN-004-R** | QCA7000  | –40 to +85 °C   | SPI           | North America   | Tape & Reel (370 pcs) |

**13.1 Available Accessories**

Chargebyte GmbH offers tested **powerline coupling transformers** that are used in the reference coupling circuits (Figures 6 and 7). These transformers can be ordered for use with the PLC Stamp micro 2 to simplify design and ensure proper operation. Refer to the transformer datasheets for full specifications.

*Table 10 – Available Coupling Transformers (Accessories).*

| **Transformer Type**                                                          | **Order Code** |
| ----------------------------------------------------------------------------- | -------------- |
| 1:4:5 ratio (for mains power line coupling)                                   | **I2PLCTR-1**  |
| 1:1:1 ratio (for Electric Vehicle **PEV** or **EVSE** pilot line) – version 1 | **I2PLCTR-2**  |
| 1:1:1 ratio (for EV PEV/EVSE pilot line) – version 2 (alternative)            | **I2PLCTR-5**  |

*(These transformers provide the necessary impedance matching and isolation for coupling the PLC signal to the line. The 1:4:5 transformer is for high-voltage mains, while the 1:1:1 versions are for low-voltage pilot lines in EV applications.)*

## 14. Packaging Information

### 14.1 Tape and Reel

Modules can be supplied on tape and reel, compliant with **EIA-481** standard dimensions (see Figure 9). The tape pocket and hole dimensions are given below (all in millimeters).

*Figure 9 – Tape and Reel Pocket Dimensions (EIA-481 Standard).*

| Parameter                                | Value           | Parameter                               | Value           |
| ---------------------------------------- | --------------- | --------------------------------------- | --------------- |
| **Ao** (pocket length)                   | 22.6 ± 0.15 mm  | **Ko** (pocket depth)                   | 5.80 ± 0.15 mm  |
| **Bo** (pocket width)                    | 22.7 ± 0.15 mm  | **K1** (pocket depth to lid)\*          | 4.80 ± 0.15 mm  |
| **Do** (sprocket hole dia.)              | Ø 1.5 +0.1 mm   | **P₀** (hole pitch)\*\*                 | 4.00 ± 0.15 mm  |
| **D1** (hole to pocket clearance)        | Ø 2.0 (min) mm  | **P₁** (pocket pitch)                   | 28.00 ± 0.15 mm |
| **E1** (reel side margin)                | 1.75 ± 0.10 mm  | **P₂** (hole-center to pocket-center)\* | 2.00 ± 0.15 mm  |
| **F** (hole-center to pocket-center)\*\* | 20.20 ± 0.15 mm | **S₀** (pocket spacing)                 | 40.40 ± 0.15 mm |
| **T** (tape thickness)                   | 0.40 ± 0.04 mm  | **W** (tape width)                      | 44.0 ± 0.3 mm   |

*All dimensions in millimeters unless otherwise stated.* Material: *Polystyrene*.
*(I)* P₂ is measured from centerline of sprocket hole to centerline of pocket.
*(II)* Cumulative tolerance of 10 sprocket holes is ±0.20 mm.
*(III)* F is measured from centerline of sprocket hole to centerline of sprocket track (across tape width).

Additional tape/reel specifications:

* **Reel inner diameter:** 4 inch (nominal)
* **Reel outer diameter:** 13 inch (nominal)
* **Reel inner width:** \~44 mm (to accommodate tape width)
* **Parts per reel:** 370 modules per 13" reel
* **Leader tape (empty pockets):** 7–12 empty pockets at beginning of tape
* **Trailer tape (empty pockets):** 7–12 empty pockets at end of tape

### 14.2 Orientation of the Module

Figure 10 shows the orientation of the module within the tape pocket. The module’s Pin 1 corner and labeling are oriented in a consistent direction relative to the tape feed. Typically, an arrow on the diagram indicates the direction of feed, and the sprocket holes in the tape are on one side. **Figure 10 – Orientation of the Module in Tape.** (The module is positioned such that Pin 1 is, for example, at the lower left of the pocket when the tape is fed with sprocket holes on the right-hand side. This ensures all modules face the same way for automated placement.)

### 14.3 Tape and Reel Cardboard Box Dimensions

For tape-and-reel shipments, reels are packed in a cardboard box. **Figure 11** provides the box dimensions:

* **H (Height):** 65 mm
* **W (Width):** 340 mm
* **L (Length):** 340 mm

*Figure 11 – Tape and Reel Packing Box Dimensions.* (The standard reel box is approximately 34 × 34 cm square and 6.5 cm deep, suitable for a 13" reel. Boxes are typically made of cardboard and hold a single reel.)

## 15. Table of non-standard versions (only on request)

In addition to the standard variants (section 13), the following tables list **non-standard or legacy versions** of PLC Stamp micro 2, which are available on request. These include configurations with the older QCA7000 chip or UART-interface variants not in the standard list, as well as modules with legacy firmware. Entries marked with **`*`** are legacy versions not recommended for new designs.

*Table 11 – PLC Stamp micro 2 Non-Standard/Legacy Order Codes* (available on request; \* = not recommended for new designs).

| **Order Code**           | **Chip** | **Temp. Range** | **Serial IF** | **Param. Opt.** | **Packaging**         |
| ------------------------ | -------- | --------------- | ------------- | --------------- | --------------------- |
| I2PLCAMC-ISC-004-T       | QCA7000  | –40 to +85 °C   | SPI           | CE Class B      | Tray (40 pcs)         |
| I2PLCAMC-ISC-004-R       | QCA7000  | –40 to +85 °C   | SPI           | CE Class B      | Tape & Reel (370 pcs) |
| I2PLCAMC-ISN-004-T       | QCA7000  | –40 to +85 °C   | SPI           | North America   | Tray (40 pcs)         |
| I2PLCAMC-ISN-004-R       | QCA7000  | –40 to +85 °C   | SPI           | North America   | Tape & Reel (370 pcs) |
| I2PLCAMC-ISE-004-T       | QCA7000  | –40 to +85 °C   | SPI           | Automotive EVSE | Tray (40 pcs)         |
| I2PLCAMC-ISE-004-R       | QCA7000  | –40 to +85 °C   | SPI           | Automotive EVSE | Tape & Reel (370 pcs) |
| I2PLCAMC-ISP-004-T       | QCA7000  | –40 to +85 °C   | SPI           | Automotive PEV  | Tray (40 pcs)         |
| I2PLCAMC-ISP-004-R       | QCA7000  | –40 to +85 °C   | SPI           | Automotive PEV  | Tape & Reel (370 pcs) |
| I2PLCAMC-IUC-004-T       | QCA7000  | –40 to +85 °C   | UART          | CE Class B      | Tray (40 pcs)         |
| I2PLCAMC-IUC-004-R       | QCA7000  | –40 to +85 °C   | UART          | CE Class B      | Tape & Reel (370 pcs) |
| I2PLCAMC-IUN-004-T       | QCA7000  | –40 to +85 °C   | UART          | North America   | Tray (40 pcs)         |
| I2PLCAMC-IUN-004-R       | QCA7000  | –40 to +85 °C   | UART          | North America   | Tape & Reel (370 pcs) |
| I2PLCAMC-IUE-004-T       | QCA7000  | –40 to +85 °C   | UART          | Automotive EVSE | Tray (40 pcs)         |
| I2PLCAMC-IUE-004-R       | QCA7000  | –40 to +85 °C   | UART          | Automotive EVSE | Tape & Reel (370 pcs) |
| I2PLCAMC-IUP-004-T       | QCA7000  | –40 to +85 °C   | UART          | Automotive PEV  | Tray (40 pcs)         |
| I2PLCAMC-IUP-004-R       | QCA7000  | –40 to +85 °C   | UART          | Automotive PEV  | Tape & Reel (370 pcs) |
| I2PLCBMC-IUC-004-T       | QCA7000  | –40 to +85 °C   | UART          | CE Class B      | Tray (40 pcs)         |
| I2PLCBMC-IUC-004-R       | QCA7000  | –40 to +85 °C   | UART          | CE Class B      | Tape & Reel (370 pcs) |
| I2PLCBMC-IUN-004-T       | QCA7000  | –40 to +85 °C   | UART          | North America   | Tray (40 pcs)         |
| I2PLCBMC-IUN-004-R       | QCA7000  | –40 to +85 °C   | UART          | North America   | Tape & Reel (370 pcs) |
| I2PLCBMC-IUE-004-T       | QCA7000  | –40 to +85 °C   | UART          | Automotive EVSE | Tray (40 pcs)         |
| I2PLCBMC-IUE-004-R       | QCA7000  | –40 to +85 °C   | UART          | Automotive EVSE | Tape & Reel (370 pcs) |
| I2PLCBMC-IUP-004-T       | QCA7000  | –40 to +85 °C   | UART          | Automotive PEV  | Tray (40 pcs)         |
| I2PLCBMC-IUP-004-R       | QCA7000  | –40 to +85 °C   | UART          | Automotive PEV  | Tape & Reel (370 pcs) |
| **I2PLCAMC-ISC-002-T**\* | QCA7000  | –40 to +85 °C   | SPI           | CE Class B      | Tray (40 pcs)         |
| **I2PLCAMC-ISC-002-R**\* | QCA7000  | –40 to +85 °C   | SPI           | CE Class B      | Tape & Reel (370 pcs) |
| **I2PLCAMC-ISN-002-T**\* | QCA7000  | –40 to +85 °C   | SPI           | North America   | Tray (40 pcs)         |
| **I2PLCAMC-ISN-002-R**\* | QCA7000  | –40 to +85 °C   | SPI           | North America   | Tape & Reel (370 pcs) |
| **I2PLCAMC-ISE-002-T**\* | QCA7000  | –40 to +85 °C   | SPI           | Automotive EVSE | Tray (40 pcs)         |
| **I2PLCAMC-ISE-002-R**\* | QCA7000  | –40 to +85 °C   | SPI           | Automotive EVSE | Tape & Reel (370 pcs) |
| **I2PLCAMC-ISP-002-T**\* | QCA7000  | –40 to +85 °C   | SPI           | Automotive PEV  | Tray (40 pcs)         |
| **I2PLCAMC-ISP-002-R**\* | QCA7000  | –40 to +85 °C   | SPI           | Automotive PEV  | Tape & Reel (370 pcs) |
| **I2PLCAMC-IUC-002-T**\* | QCA7000  | –40 to +85 °C   | UART          | CE Class B      | Tray (40 pcs)         |
| **I2PLCAMC-IUC-002-R**\* | QCA7000  | –40 to +85 °C   | UART          | CE Class B      | Tape & Reel (370 pcs) |
| **I2PLCAMC-IUN-002-T**\* | QCA7000  | –40 to +85 °C   | UART          | North America   | Tray (40 pcs)         |
| **I2PLCAMC-IUN-002-R**\* | QCA7000  | –40 to +85 °C   | UART          | North America   | Tape & Reel (370 pcs) |
| **I2PLCAMC-IUE-002-T**\* | QCA7000  | –40 to +85 °C   | UART          | Automotive EVSE | Tray (40 pcs)         |
| **I2PLCAMC-IUE-002-R**\* | QCA7000  | –40 to +85 °C   | UART          | Automotive EVSE | Tape & Reel (370 pcs) |
| **I2PLCAMC-IUP-002-T**\* | QCA7000  | –40 to +85 °C   | UART          | Automotive PEV  | Tray (40 pcs)         |
| **I2PLCAMC-IUP-002-R**\* | QCA7000  | –40 to +85 °C   | UART          | Automotive PEV  | Tape & Reel (370 pcs) |
| **I2PLCAMC-CSC-002-T**\* | QCA7000  | 0 to +70 °C     | SPI           | CE Class B      | Tray (40 pcs)         |
| **I2PLCAMC-CSC-002-R**\* | QCA7000  | 0 to +70 °C     | SPI           | CE Class B      | Tape & Reel (370 pcs) |
| **I2PLCAMC-CSN-002-T**\* | QCA7000  | 0 to +70 °C     | SPI           | North America   | Tray (40 pcs)         |
| **I2PLCAMC-CSN-002-R**\* | QCA7000  | 0 to +70 °C     | SPI           | North America   | Tape & Reel (370 pcs) |
| **I2PLCAMC-CSE-002-T**\* | QCA7000  | 0 to +70 °C     | SPI           | Automotive EVSE | Tray (40 pcs)         |
| **I2PLCAMC-CSE-002-R**\* | QCA7000  | 0 to +70 °C     | SPI           | Automotive EVSE | Tape & Reel (370 pcs) |
| **I2PLCAMC-CSP-002-T**\* | QCA7000  | 0 to +70 °C     | SPI           | Automotive PEV  | Tray (40 pcs)         |
| **I2PLCAMC-CSP-002-R**\* | QCA7000  | 0 to +70 °C     | SPI           | Automotive PEV  | Tape & Reel (370 pcs) |
| **I2PLCAMC-CUC-002-T**\* | QCA7000  | 0 to +70 °C     | UART          | CE Class B      | Tray (40 pcs)         |
| **I2PLCAMC-CUC-002-R**\* | QCA7000  | 0 to +70 °C     | UART          | CE Class B      | Tape & Reel (370 pcs) |
| **I2PLCAMC-CUN-002-T**\* | QCA7000  | 0 to +70 °C     | UART          | North America   | Tray (40 pcs)         |
| **I2PLCAMC-CUN-002-R**\* | QCA7000  | 0 to +70 °C     | UART          | North America   | Tape & Reel (370 pcs) |
| **I2PLCAMC-CUE-002-T**\* | QCA7000  | 0 to +70 °C     | UART          | Automotive EVSE | Tray (40 pcs)         |
| **I2PLCAMC-CUE-002-R**\* | QCA7000  | 0 to +70 °C     | UART          | Automotive EVSE | Tape & Reel (370 pcs) |
| **I2PLCAMC-CUP-002-T**\* | QCA7000  | 0 to +70 °C     | UART          | Automotive PEV  | Tray (40 pcs)         |
| **I2PLCAMC-CUP-002-R**\* | QCA7000  | 0 to +70 °C     | UART          | Automotive PEV  | Tape & Reel (370 pcs) |
| **I2PLCBMC-ISC-002-T**\* | QCA7000  | –40 to +85 °C   | SPI           | CE Class B      | Tray (40 pcs)         |
| **I2PLCBMC-ISC-002-R**\* | QCA7000  | –40 to +85 °C   | SPI           | CE Class B      | Tape & Reel (370 pcs) |
| **I2PLCBMC-ISN-002-T**\* | QCA7000  | –40 to +85 °C   | SPI           | North America   | Tray (40 pcs)         |
| **I2PLCBMC-ISN-002-R**\* | QCA7000  | –40 to +85 °C   | SPI           | North America   | Tape & Reel (370 pcs) |
| **I2PLCBMC-ISE-002-T**\* | QCA7000  | –40 to +85 °C   | SPI           | Automotive EVSE | Tray (40 pcs)         |
| **I2PLCBMC-ISE-002-R**\* | QCA7000  | –40 to +85 °C   | SPI           | Automotive EVSE | Tape & Reel (370 pcs) |
| **I2PLCBMC-ISP-002-T**\* | QCA7000  | –40 to +85 °C   | SPI           | Automotive PEV  | Tray (40 pcs)         |
| **I2PLCBMC-ISP-002-R**\* | QCA7000  | –40 to +85 °C   | SPI           | Automotive PEV  | Tape & Reel (370 pcs) |
| **I2PLCBMC-IUC-002-T**\* | QCA7000  | –40 to +85 °C   | UART          | CE Class B      | Tray (40 pcs)         |
| **I2PLCBMC-IUC-002-R**\* | QCA7000  | –40 to +85 °C   | UART          | CE Class B      | Tape & Reel (370 pcs) |
| **I2PLCBMC-IUN-002-T**\* | QCA7000  | –40 to +85 °C   | UART          | North America   | Tray (40 pcs)         |
| **I2PLCBMC-IUN-002-R**\* | QCA7000  | –40 to +85 °C   | UART          | North America   | Tape & Reel (370 pcs) |
| **I2PLCBMC-IUE-002-T**\* | QCA7000  | –40 to +85 °C   | UART          | Automotive EVSE | Tray (40 pcs)         |
| **I2PLCBMC-IUE-002-R**\* | QCA7000  | –40 to +85 °C   | UART          | Automotive EVSE | Tape & Reel (370 pcs) |
| **I2PLCBMC-IUP-002-T**\* | QCA7000  | –40 to +85 °C   | UART          | Automotive PEV  | Tray (40 pcs)         |
| **I2PLCBMC-IUP-002-R**\* | QCA7000  | –40 to +85 °C   | UART          | Automotive PEV  | Tape & Reel (370 pcs) |

*Note:* **`*`** indicates legacy versions (generally using older QCA7000 silicon or older firmware) which are **not recommended for new designs**. These are typically provided for continuity in existing projects.

## 16. Contact

**chargebyte GmbH**
Bitterfelder Straße 1–5
04103 Leipzig
Germany

Website: [https://www.chargebyte.com](https://www.chargebyte.com)&#x20;
