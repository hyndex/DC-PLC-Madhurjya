# AN4 QCA7000 SPI / UART Protocol

*I2SE GmbH: Christian Aurich, Stefan Wahren*
*June 14, 2018*

**Contents**

* 1 Revisions (page 3)
* 2 Introduction (page 3)
* 3 SPI Physical Layer (page 3)
* 4 UART Physical Layer (page 3)
* 5 SPI Command Structure (page 4)

  * 5.1 QCA7000 SPI registers (page 5)

    * 5.1.1 SPI REG BFR SIZE (page 5)
    * 5.1.2 SPI REG WRBUF SPC AVA (page 5)
    * 5.1.3 SPI REG RDBUF BYTE AVA (page 5)
    * 5.1.4 SPI REG SPI CONFIG (page 6)
    * 5.1.5 SPI REG INTR CAUSE (page 6)
    * 5.1.6 SPI REG INTR ENABLE (page 6)
    * 5.1.7 SPI REG SIGNATURE (page 6)
* 6 Framing for Ethernet Packets (page 7)

  * 6.1 Transmit Frame (page 7)
  * 6.2 SPI Receive Frame (page 7)
  * 6.3 UART Receive Frame (page 8)
* 7 Recommended Usage of the SPI (page 9)

  * 7.1 Initial Setup after each QCA7000 reset (page 9)
  * 7.2 Handling SPI Interrupts (page 9)
  * 7.3 External Read (page 10)
  * 7.4 External Write (page 11)
* 8 Troubleshooting (page 11)
* 9 Appendix (page 11)
* 10 Contact (page 11)

## 1 Revisions

* **5** (June 16, 2018): add I2SE address, add example Ethernet frame, fix frame offset in UART transmit description
* **4** (February 7, 2017): additionally describe UART interface to QCA7000
* **3** (April 1, 2016): add SPI data width, add diagrams, add OSI model, fix initial setup, fix interrupt handling, avoid misunderstandings
* **2** (April 21, 2015): added SPI CLK speed, changed EOF fields of transmit frames to length 2
* **1** (September 30, 2014): initial release

## 2 Introduction

This application note aims to describe the QCA7000 PLC chip SPI/UART interface. The documentation was derived from the original Qualcomm Atheros driver for the QCA7000, which was initially published at [https://github.com/IoE/qca7000](https://github.com/IoE/qca7000). All information within this document can be derived from this code. The basis for this document is the status of the driver at the time this document was released initially. Its focus is on layers 1 and 2 of the OSI model.

**OSI layer vs. Protocol vs. Software component:**

| OSI layer        | Protocol          | Software component                     |
| ---------------- | ----------------- | -------------------------------------- |
| 7 – Application  | Application       | Application                            |
| 6 – Presentation | *(not specified)* | *(not specified)*                      |
| 5 – Session      | *(not specified)* | *(not specified)*                      |
| 4 – Transport    | TCP/UDP           | TCP/UDP stack                          |
| 3 – Network      | IPv4/IPv6         | IP stack                               |
| 2 – Data link    | Ethernet          | Ethernet-over-SPI/UART protocol driver |
| 1 – Physical     | SPI/UART          | SPI/UART driver                        |

I2SE GmbH does not give any free support for driver development. Please contact us if you want to know the conditions of our paid support.

**Note:** During development we strongly suggest using a logic analyzer with the following features:

* at least 5 channels
* at least 24 MHz sampling rate
* SPI/UART decoding

## 3 SPI Physical Layer

The QCA7000 acts as a SPI slave and uses Mode 3 (CPOL=1, CPHA=1). SPI data width is 8 bit. The SPI CLK period should not be less than 83.3 ns. The SPI should be used in burst mode, meaning that the chip select is held low during a complete SPI message.

**Note:** The SPI lines between the host CPU and QCA7000 should be kept as short as possible.

## 4 UART Physical Layer

The QCA7000 has a 4-wire UART interface (TX, RX, CTS, RTS) where the hardware flow control is optional. Several UART settings are possible and are configurable with tools that are available under NDA from Qualcomm. I2SE chose the settings in **Table 4** as a default for all UART-interfaced modules.

| Setting      | Value  |
| ------------ | ------ |
| Baud Rate    | 115200 |
| Data Bits    | 8      |
| Parity       | None   |
| Stop Bits    | 1      |
| Flow Control | None   |

*Table 4: Default UART Settings*

## 5 SPI Command Structure

The general function of the SPI protocol is to first send a two-byte command and then send or receive the associated data. (The following interpretation is the same as on the SPI bus: most significant bit first.)

| BIT     | 15         | 14                | 13–0     |
| ------- | ---------- | ----------------- | -------- |
| MEANING | Read/Write | Internal/External | Register |

* **Read/Write:** read = 1 / write = 0
* **Internal/External:** Internal = 1 / External = 0. Internal is used to read or write SPI registers of the local QCA7000; external is used to read or write Ethernet data.
* **Register:**

  * if Internal/External = 1: the register to read from or write to (as defined in the list of QCA7000 SPI registers)
  * if Internal/External = 0: the complete register must be zero

**Figure 1:** Example – Read SPI REG SIGNATURE (1/2). *Signal timing diagram showing an example of reading the SPI\_REG\_SIGNATURE register (part 1 of 2). The diagram shows the Chip Select (CS), Clock (CLK), MOSI, and MISO lines for the first 17 bits of the SPI transaction (including the command frame with register address 0x1A00).*

**Figure 2:** Example – Read SPI REG SIGNATURE (2/2). *Signal timing diagram showing the continuation of the SPI\_REG\_SIGNATURE read sequence (part 2 of 2). The diagram shows bits 17–33 of the SPI transaction, where the QCA7000 returns the 16-bit signature value 0xAA55 on the MISO line.*

### 5.1 QCA7000 SPI registers

| Register Name              | Register Address | Access | Value width |
| -------------------------- | ---------------- | ------ | ----------- |
| SPI\_REG\_BFR\_SIZE        | 0x0100           | W      | 16 bit      |
| SPI\_REG\_WRBUF\_SPC\_AVA  | 0x0200           | R      | 16 bit      |
| SPI\_REG\_RDBUF\_BYTE\_AVA | 0x0300           | R      | 16 bit      |
| SPI\_REG\_SPI\_CONFIG      | 0x0400           | R/W    | 16 bit      |
| SPI\_REG\_INTR\_CAUSE      | 0x0C00           | R/W    | 16 bit      |
| SPI\_REG\_INTR\_ENABLE     | 0x0D00           | R/W    | 16 bit      |
| SPI\_REG\_SIGNATURE        | 0x1A00           | R      | 16 bit      |

*Note:* The Access column is derived from the actual function of the original driver. It may be that some registers can also be accessed otherwise.

#### 5.1.1 SPI REG BFR SIZE

This register has to be set before reading or writing external data. It needs to contain the byte count of the data to be accessed over SPI.

#### 5.1.2 SPI REG WRBUF SPC AVA

This register can be read to determine how much space is available in the write buffer. It contains the byte count of data that can be written via the write external command. This register should have the value 0x0C5B (3163 bytes) after a reset, as long as no data was sent before via an external write command. Reserving more data with `SPI_REG_BFR_SIZE` than available via `SPI_REG_WRBUF_SPC_AVA` isn’t allowed.

#### 5.1.3 SPI REG RDBUF BYTE AVA

This register can be read to determine how many bytes are available in the read buffer. It contains the byte count of data that can be read via the read external command. Requesting more data with `SPI_REG_BFR_SIZE` than available via `SPI_REG_RDBUF_BYTE_AVA` isn’t allowed.

#### 5.1.4 SPI REG SPI CONFIG

This register contains “settings” of the SPI interface. **Meaning of bits:**

* **`QCASPI_SLAVE_RESET_BIT (1 << 6)`:** Enabling this bit causes the QCA7000 to reset.

All the other bits should remain unchanged. Therefore, this register should be used in a “read, modify, write” cycle.

#### 5.1.5 SPI REG INTR CAUSE

This register can be queried to find out the reason for a hardware interrupt on the dedicated SPI interrupt line. **Meaning of bits** (same as in `SPI_REG_INTR_ENABLE`):

```
SPI_INT_CPU_ON   (1 << 6)
SPI_INT_WRBUF_ERR (1 << 2)
SPI_INT_RDBUF_ERR (1 << 1)
SPI_INT_PKT_AVLBL (1 << 0)
```

This register can also be written to in order to confirm the SPI interrupt.

#### 5.1.6 SPI REG INTR ENABLE

This register contains the enable flags for different SPI interrupts. **Meaning of bits** (same as in `SPI_REG_INTR_CAUSE`):

```
SPI_INT_CPU_ON   (1 << 6)
SPI_INT_WRBUF_ERR (1 << 2)
SPI_INT_RDBUF_ERR (1 << 1)
SPI_INT_PKT_AVLBL (1 << 0)
```

#### 5.1.7 SPI REG SIGNATURE

This register can be used to ensure the correct function of the SPI interface and that the host handles the endianness correctly. It always contains 0xAA55.

## 6 Framing for Ethernet Packets

All Ethernet packets must be framed as described in this chapter. Additionally, for the SPI interface these frames must be encapsulated in external read/write operations.

*Example Ethernet frame (without SPI/UART framing – ARP broadcast, padded to 60 bytes):*

```
FF FF FF FF FF FF 06 05 04 03 02 03 08 06 00 01  
08 00 06 04 00 01 06 05 04 03 02 03 C0 A8 01 04  
00 00 00 00 00 00 C0 A8 01 04 00 00 00 00 00 00  
00 00 00 00 00 00 00 00 00 00 00 00  
```

### 6.1 Transmit Frame

The SPI/UART transmit framing is used for data packets sent from the host processor to the QCA7000.

| Offset          | Length  | Symbol         | Description                                                                                                                                                                                                      |
| --------------- | ------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0x0000          | 4       | SOF            | Start Of Frame. Must be 0xAAAAAAAA.                                                                                                                                                                              |
| 0x0004          | 2       | FL             | The Ethernet frame length in little-endian format. The frame starts at offset 0x0008 and includes all fields up to but excluding EOF. The minimum is 60. The maximum is 1518 if VLAN is omitted and 1522 if not. |
| 0x0006          | 2       | RSVD           | Must be 0x0000. Reserved to ensure 4-byte frame alignment.                                                                                                                                                       |
| 0x0008          | 60–1522 | ETH Frame Body | (Layer 2 Ethernet frame without FCS), contains Destination Address, Source Address, (VLAN Tag), Ethertype and the actual data.                                                                                   |
| 0x004A to 0x5F8 | 2       | EOF            | End Of Frame. Must be 0x5555. This field starts at offset 0x0008 plus FL.                                                                                                                                        |

*Note:* In case the frame body is smaller than 60 bytes, it needs to be padded with zero.

### 6.2 SPI Receive Frame

The SPI receive framing is used for data packets sent from the QCA7000 to the host processor.

| Offset                        | Length  | Symbol         | Description                                                                                                                                                                                                      |
| ----------------------------- | ------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0x0000                        | 4       | LEN            | Hardware-generated packet length.                                                                                                                                                                                |
| 0x0004                        | 4       | SOF            | Start Of Frame. Must be 0xAAAAAAAA.                                                                                                                                                                              |
| 0x0008                        | 2       | FL             | The Ethernet frame length in little-endian format. The frame starts at offset 0x000C and includes all fields up to but excluding EOF. The minimum is 60. The maximum is 1518 if VLAN is omitted and 1522 if not. |
| 0x000A                        | 2       | RSVD           | Must be 0x0000. Reserved to ensure 4-byte frame alignment.                                                                                                                                                       |
| 0x000C                        | 60–1522 | ETH Frame Body | Layer 2 Ethernet frame without FCS, contains Destination MAC Address, Source MAC Address, optional VLAN Tag, Ethertype and the actual data.                                                                      |
| 0x0008 + FL (0x004A to 0x5F8) | 2       | EOF            | End Of Frame. Must be 0x5555.                                                                                                                                                                                    |

### 6.3 UART Receive Frame

The UART receive framing is used for data packets sent from the QCA7000 to the host processor.

| Offset                        | Length  | Symbol         | Description                                                                                                                                                                                                      |
| ----------------------------- | ------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0x0000                        | 4       | SOF            | Start Of Frame. Must be 0xAAAAAAAA.                                                                                                                                                                              |
| 0x0004                        | 2       | FL             | The Ethernet frame length in little-endian format. The frame starts at offset 0x0008 and includes all fields up to but excluding EOF. The minimum is 60. The maximum is 1518 if VLAN is omitted and 1522 if not. |
| 0x0006                        | 2       | RSVD           | Must be 0x0000. Reserved to ensure 4-byte frame alignment.                                                                                                                                                       |
| 0x0008                        | 60–1522 | ETH Frame Body | Layer 2 Ethernet frame without FCS, contains Destination MAC Address, Source MAC Address, optional VLAN Tag, Ethertype and the actual data.                                                                      |
| 0x0004 + FL (0x004A to 0x5F8) | 2       | EOF            | End Of Frame. Must be 0x5555.                                                                                                                                                                                    |

## 7 Recommended Usage of the SPI

### 7.1 Initial Setup after each QCA7000 reset

To initialize the SPI interface after each QCA7000 reset, perform the following steps:

1. Read `SPI_REG_SIGNATURE` and ignore the response.
2. Read `SPI_REG_SIGNATURE` again and make sure it matches the defined value of 0xAA55.
3. Set the `SPI_REG_INTR_ENABLE` register, enabling the following interrupts: `SPI_INT_CPU_ON`, `SPI_INT_PKT_AVLBL`, `SPI_INT_RDBUF_ERR`, `SPI_INT_WRBUF_ERR`.

### 7.2 Handling SPI Interrupts

The QCA7000 has an interrupt signal to indicate that it has something to tell the host CPU. If the host CPU captures a rising edge on the interrupt signal (or a falling edge when the IRQ line is active-low), all interrupts should first be disabled by writing zero to the `SPI_REG_INTR_ENABLE` register. After that, the `SPI_REG_INTR_CAUSE` register can be read. In this register, the QCA7000 indicates why the interrupt was raised.

Once the content of the interrupt cause register is received, the interrupt cause should be confirmed by writing the exact same content back. This resets all interrupt causes – so make sure to react to all of them correctly. Resetting all interrupt causes results in the SPI interrupt signal transitioning back to low. Finally, re-enable all desired interrupts in the `SPI_REG_INTR_ENABLE` register.

**Figure 3:** Example – Interrupt handling. *Signal timing diagram illustrating SPI interrupt handling. The traces for Chip Select (CS), Clock (CLK), MOSI, MISO, and the SPI interrupt line are shown during an interrupt event and the subsequent handling routine.*

**Reaction for interrupt causes:**

| Interrupt cause     | Recommended reaction                                        |
| ------------------- | ----------------------------------------------------------- |
| `SPI_INT_CPU_ON`    | The QCA7000 just performed a startup (see “initial setup”). |
| `SPI_INT_WRBUF_ERR` | Perform a QCA7000 restart via `QCASPI_SLAVE_RESET_BIT`.     |
| `SPI_INT_RDBUF_ERR` | Perform a QCA7000 restart via `QCASPI_SLAVE_RESET_BIT`.     |
| `SPI_INT_PKT_AVLBL` | Perform an external read.                                   |

### 7.3 External Read

To read data from the QCA7000 via an *external read* operation, the following steps should be performed:

1. Read register `SPI_REG_RDBUF_BYTE_AVA` – this gives the length of the available data.
2. Write register `SPI_REG_BFR_SIZE` – this sets the length of data to be read via the “external read”.
3. Start an external read and receive as much data as specified by `SPI_REG_BFR_SIZE`.

**Note:** The SPI chip select (CS) must be kept low between the external read command and the data reception.

**Figure 4:** Example – External read. *Signal timing diagram illustrating an example external read operation. The CS line stays low while the host issues a read command on MOSI and the QCA7000 returns the requested data on MISO, with the SPI interrupt line indicating data availability.*

### 7.4 External Write

To write data to the QCA7000 via an *external write*, the following steps should be performed:

1. Calculate the value for `SPI_REG_BFR_SIZE` (i.e. `value = SOF + FL + RSVD + ETH + EOF`).
2. Read register `SPI_REG_WRBUF_SPC_AVA` to find out how much space is available in the QCA7000.
3. Write register `SPI_REG_BFR_SIZE` to set the length of data to be sent via the next “external write”.
4. Start an external write and send as much data as specified by `SPI_REG_BFR_SIZE`.

**Note:** The SPI chip select must be kept low between the external write command and writing the data.

**Figure 5:** Example – External write. *Signal timing diagram illustrating an example external write operation. The CS line remains low while the host sends a write command and subsequently transmits data over MOSI to the QCA7000, with corresponding acknowledgement on the SPI interrupt line.*

## 8 Troubleshooting

If your SPI communication is not working, you should try the following:

* Keep the SPI signal lines as short as possible, preferably under 10 cm.
* If the response of the QCA7000 for the signature register seems to be incorrect, try adding series termination resistors (between 10 Ω and 20 Ω) in the SPI clock and data lines.

## 9 Contact

I2SE GmbH
Friedrich-List-Platz 2
04103 Leipzig
Germany
[http://www.i2se.com/](http://www.i2se.com/)
