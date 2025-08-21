import os
import fcntl
import struct
import subprocess
import threading
import logging
from typing import Optional

from plc_communication.plc_network import PLCNetwork

"""Bridge PLC traffic to a TAP interface."""


logger = logging.getLogger(__name__)

# Constants for TAP device creation
TUNSETIFF = 0x400454ca
IFF_TAP = 0x0002
IFF_NO_PI = 0x1000

# Name of the TAP device we create
TAP_NAME = b"tap0"

# Maximum size of an Ethernet frame (including VLAN tag)
ETH_FRAME_MAX = 1522


class TapInterfaceError(Exception):
    """Raised when the TAP interface cannot be created or configured."""


def create_tap_interface():
    """Create and return a TAP interface file descriptor and name."""
    try:
        tap_fd = os.open("/dev/net/tun", os.O_RDWR)
        ifr = struct.pack("16sH", TAP_NAME, IFF_TAP | IFF_NO_PI)
        fcntl.ioctl(tap_fd, TUNSETIFF, ifr)
        return tap_fd, TAP_NAME.decode()
    except OSError as e:
        logger.error("Error creating TAP device: %s", e)
        raise TapInterfaceError(f"Error creating TAP device: {e}") from e


def configure_tap_interface(interface_name, ip_address, netmask):
    """Configure the TAP interface with the given IP address and netmask."""
    try:
        subprocess.run(
            [
                "ip",
                "addr",
                "add",
                f"{ip_address}/{netmask}",
                "dev",
                interface_name,
            ],
            check=True,
        )
        subprocess.run(
            ["ip", "link", "set", "dev", interface_name, "up"],
            check=True,
        )
        logger.debug(
            "Configured TAP interface %s with %s/%s",
            interface_name,
            ip_address,
            netmask,
        )
    except subprocess.CalledProcessError as e:
        logger.error("Failed to configure TAP interface %s: %s", interface_name, e)
        raise TapInterfaceError(
            f"Failed to configure TAP interface {interface_name}"
        ) from e


def plc_to_tap(plc, tap_fd, stop_event: Optional[threading.Event] = None):
    """Read from the PLC and write to the TAP interface."""
    while not (stop_event and stop_event.is_set()):
        try:
            frame = plc.recv()
        except Exception:  # pragma: no cover - unexpected PLC errors
            logger.exception("Error receiving frame from PLC")
            raise
        if frame:
            try:
                os.write(tap_fd, bytes(frame))
                logger.debug("Forwarded %d bytes PLC→TAP", len(frame))
            except Exception:  # pragma: no cover - write errors
                logger.exception("Error writing frame to TAP interface")
                raise


def tap_to_plc(plc, tap_fd, stop_event: Optional[threading.Event] = None):
    """Read from the TAP interface and write to the PLC."""
    while not (stop_event and stop_event.is_set()):
        try:
            packet = os.read(tap_fd, ETH_FRAME_MAX)
        except Exception:  # pragma: no cover - read errors
            logger.exception("Error reading from TAP interface")
            raise
        if packet:
            try:
                plc.send(list(packet))
                logger.debug("Forwarded %d bytes TAP→PLC", len(packet))
            except Exception:  # pragma: no cover - send errors
                logger.exception("Error sending packet to PLC")
                raise


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
