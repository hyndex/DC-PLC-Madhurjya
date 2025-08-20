from .qca7000 import QCA7000

class PLCNetwork:
    def __init__(self, spi_bus=0, spi_device=0):
        self.qca = QCA7000(spi_bus, spi_device)
        self.qca.initialize()

    def send(self, frame):
        # Add the framing required by the QCA7000
        # SOF (4 bytes) + Frame Length (2 bytes) + Reserved (2 bytes) + Frame + EOF (2 bytes)
        sof = [0xAA, 0xAA, 0xAA, 0xAA]
        frame_len = len(frame).to_bytes(2, 'little')
        rsvd = [0x00, 0x00]
        eof = [0x55, 0x55]

        # Pad the frame to a minimum of 60 bytes
        if len(frame) < 60:
            frame += [0x00] * (60 - len(frame))

        qca_frame = sof + list(frame_len) + rsvd + list(frame) + eof
        self.qca.write_ethernet_frame(qca_frame)

    def recv(self):
        qca_frame = self.qca.read_ethernet_frame()
        if not qca_frame:
            return None

        # Remove the QCA7000 framing
        # LEN (4 bytes) + SOF (4 bytes) + Frame Length (2 bytes) + Reserved (2 bytes) + Frame + EOF (2 bytes)
        frame_len = int.from_bytes(bytes(qca_frame[8:10]), 'little')
        frame = qca_frame[12 : 12 + frame_len]

        return frame
