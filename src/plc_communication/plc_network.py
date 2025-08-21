from typing import List, Optional, Union

from .qca7000 import QCA7000


ETH_FRAME_MIN = 60
ETH_FRAME_MAX = 1522

class PLCNetwork:
    def __init__(self, spi_bus=0, spi_device=0):
        self.qca = QCA7000(spi_bus, spi_device)
        self.qca.initialize()

    def close(self) -> None:
        """Close the underlying QCA7000 device."""
        self.qca.close()

    def send(self, frame: Union[List[int], bytes]) -> None:
        """Send an Ethernet frame through the PLC.

        Args:
            frame: The Ethernet frame payload as a ``list`` of integers or a
                ``bytes`` object.

        Raises:
            ValueError: If the frame is empty or exceeds ``ETH_FRAME_MAX``
                bytes.
        """

        frame_list = list(frame)  # copy/convert to avoid mutating the input
        frame_len = len(frame_list)

        if frame_len == 0:
            raise ValueError("Frame must contain at least one byte")
        if frame_len > ETH_FRAME_MAX:
            raise ValueError(
                f"Frame length {frame_len} exceeds maximum {ETH_FRAME_MAX}"
            )

        # Add the framing required by the QCA7000
        # SOF (4 bytes) + Frame Length (2 bytes) + Reserved (2 bytes) + Frame + EOF (2 bytes)
        sof = [0xAA, 0xAA, 0xAA, 0xAA]
        frame_len_bytes = frame_len.to_bytes(2, "little")
        rsvd = [0x00, 0x00]
        eof = [0x55, 0x55]

        # Pad the frame to a minimum size
        if frame_len < ETH_FRAME_MIN:
            frame_list = frame_list + [0x00] * (ETH_FRAME_MIN - frame_len)

        qca_frame = sof + list(frame_len_bytes) + rsvd + frame_list + eof
        self.qca.write_ethernet_frame(qca_frame)

    def recv(self) -> Optional[List[int]]:
        """Receive an Ethernet frame from the PLC.

        Returns:
            A list of byte values representing the Ethernet frame payload, or
            ``None`` if no frame is available.
        """

        qca_frame = self.qca.read_ethernet_frame()
        if not qca_frame:
            return None

        # Remove the QCA7000 framing
        # LEN (4 bytes) + SOF (4 bytes) + Frame Length (2 bytes) + Reserved (2 bytes) + Frame + EOF (2 bytes)
        frame_len = int.from_bytes(bytes(qca_frame[8:10]), "little")
        frame = qca_frame[12 : 12 + frame_len]

        return frame
