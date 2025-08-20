import os
import fcntl
import struct
import threading
from plc_communication.plc_network import PLCNetwork

"""Bridge PLC traffic to a TAP interface."""

# Constants for TAP device creation
TUNSETIFF = 0x400454ca
IFF_TAP = 0x0002
IFF_NO_PI = 0x1000

# Name of the TAP device we create
TAP_NAME = b"tap0"

# Maximum size of an Ethernet frame (including VLAN tag)
ETH_FRAME_MAX = 1522


def create_tap_interface():
    """Create and return a TAP interface file descriptor and name."""
    try:
        tap_fd = os.open('/dev/net/tun', os.O_RDWR)
        ifr = struct.pack('16sH', TAP_NAME, IFF_TAP | IFF_NO_PI)
        fcntl.ioctl(tap_fd, TUNSETIFF, ifr)
        return tap_fd, TAP_NAME.decode()
    except IOError as e:
        print(f"Error creating TAP device: {e}")
        exit(1)


def configure_tap_interface(interface_name, ip_address, netmask):
    """Configure the TAP interface with the given IP address and netmask."""
    os.system(f'ip addr add {ip_address}/{netmask} dev {interface_name}')
    os.system(f'ip link set dev {interface_name} up')


def plc_to_tap(plc, tap_fd):
    """Read from the PLC and write to the TAP interface."""
    while True:
        frame = plc.recv()
        if frame:
            # Write the complete Ethernet frame to the TAP device
            os.write(tap_fd, bytes(frame))


def tap_to_plc(plc, tap_fd):
    """Read from the TAP interface and write to the PLC."""
    while True:
        # Read a full Ethernet frame from the TAP interface
        packet = os.read(tap_fd, ETH_FRAME_MAX)
        if packet:
            plc.send(list(packet))


def main():
    # Create and configure the TAP interface
    tap_fd, tap_name = create_tap_interface()
    configure_tap_interface(tap_name, '192.168.1.1', '24')

    # Create the PLC network object
    plc = PLCNetwork()

    # Create and start the threads
    plc_to_tap_thread = threading.Thread(target=plc_to_tap, args=(plc, tap_fd))
    tap_to_plc_thread = threading.Thread(target=tap_to_plc, args=(plc, tap_fd))

    plc_to_tap_thread.start()
    tap_to_plc_thread.start()

    print(
        f"TAP device '{tap_name}' is up and bridged with the PLC modem "
        f"(frame size {ETH_FRAME_MAX} bytes)."
    )
    print("Press Ctrl+C to stop.")

    try:
        plc_to_tap_thread.join()
        tap_to_plc_thread.join()
    except KeyboardInterrupt:
        print("\nClosing TAP device and stopping threads.")
        os.close(tap_fd)

if __name__ == '__main__':
    main()
