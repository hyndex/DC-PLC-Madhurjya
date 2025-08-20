import spidev
import time

# QCA7000 SPI registers
SPI_REG_BFR_SIZE = 0x0100
SPI_REG_WRBUF_SPC_AVA = 0x0200
SPI_REG_RDBUF_BYTE_AVA = 0x0300
SPI_REG_SPI_CONFIG = 0x0400
SPI_REG_INTR_CAUSE = 0x0C00
SPI_REG_INTR_ENABLE = 0x0D00
SPI_REG_SIGNATURE = 0x1A00

# QCA7000 SPI commands
SPI_CMD_READ = 0x8000
SPI_CMD_WRITE = 0x0000
SPI_CMD_INTERNAL = 0x4000
SPI_CMD_EXTERNAL = 0x0000

# QCA7000 SPI interrupt causes
SPI_INT_CPU_ON = (1 << 6)
SPI_INT_WRBUF_ERR = (1 << 2)
SPI_INT_RDBUF_ERR = (1 << 1)
SPI_INT_PKT_AVLBL = (1 << 0)

class QCA7000:
    def __init__(self, spi_bus=0, spi_device=0):
        self.spi = spidev.SpiDev()
        self.spi.open(spi_bus, spi_device)
        self.spi.max_speed_hz = 12000000  # 12 MHz
        self.spi.mode = 0b11  # SPI Mode 3

    def _spi_transfer(self, data):
        return self.spi.xfer2(data)

    def _read_register(self, register):
        command = SPI_CMD_READ | SPI_CMD_INTERNAL | register
        request = [(command >> 8) & 0xFF, command & 0xFF, 0x00, 0x00]
        response = self._spi_transfer(request)
        return (response[2] << 8) | response[3]

    def _write_register(self, register, value):
        command = SPI_CMD_WRITE | SPI_CMD_INTERNAL | register
        request = [(command >> 8) & 0xFF, command & 0xFF, (value >> 8) & 0xFF, value & 0xFF]
        self._spi_transfer(request)

    def initialize(self):
        # Recommended initialization sequence from QCA700X.md
        self._read_register(SPI_REG_SIGNATURE) # Dummy read
        signature = self._read_register(SPI_REG_SIGNATURE)
        if signature != 0xAA55:
            raise Exception(f"Invalid signature: {hex(signature)}")

        # Enable interrupts
        interrupts = SPI_INT_CPU_ON | SPI_INT_PKT_AVLBL | SPI_INT_RDBUF_ERR | SPI_INT_WRBUF_ERR
        self._write_register(SPI_REG_INTR_ENABLE, interrupts)

    def read_ethernet_frame(self):
        # Check if a packet is available
        if not (self._read_register(SPI_REG_INTR_CAUSE) & SPI_INT_PKT_AVLBL):
            return None

        # Get the length of the available data
        length = self._read_register(SPI_REG_RDBUF_BYTE_AVA)
        if length == 0:
            return None

        # Set the buffer size for the read
        self._write_register(SPI_REG_BFR_SIZE, length)

        # Read the frame
        command = SPI_CMD_READ | SPI_CMD_EXTERNAL
        request = [(command >> 8) & 0xFF, command & 0xFF]
        frame_data = self._spi_transfer(request + [0x00] * length)

        # Clear the interrupt cause
        self._write_register(SPI_REG_INTR_CAUSE, SPI_INT_PKT_AVLBL)

        return frame_data[2:] # Remove the 2-byte command from the beginning

    def write_ethernet_frame(self, frame):
        # Calculate the frame length
        frame_length = len(frame)

        # Check for available space in the write buffer
        available_space = self._read_register(SPI_REG_WRBUF_SPC_AVA)
        if frame_length > available_space:
            raise Exception("Not enough space in write buffer")

        # Set the buffer size for the write
        self._write_register(SPI_REG_BFR_SIZE, frame_length)

        # Write the frame
        command = SPI_CMD_WRITE | SPI_CMD_EXTERNAL
        request = [(command >> 8) & 0xFF, command & 0xFF]
        self._spi_transfer(request + frame)
