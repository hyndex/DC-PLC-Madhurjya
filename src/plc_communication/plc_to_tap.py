import fcntl
import logging
import os
import struct
import subprocess
import threading
from typing import Optional
from plc_communication.plc_network import PLCNetwork

"""Bridge PLC traffic to a TAP interface."""

logger = logging.getLogger(__name__)


class TapInterfaceError(Exception):
    """Raised when a TAP interface cannot be created or configured."""

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
        try:
            ifr = struct.pack('16sH', TAP_NAME, IFF_TAP | IFF_NO_PI)
            fcntl.ioctl(tap_fd, TUNSETIFF, ifr)
        except OSError as e:
            os.close(tap_fd)
            logger.error("Error configuring TAP device: %s", e)
            raise TapInterfaceError(f"Failed to configure TAP interface: {e}") from e
        return tap_fd, TAP_NAME.decode()
    except OSError as e:
        logger.error("Error creating TAP device: %s", e)
        raise TapInterfaceError(f"Failed to create TAP interface: {e}") from e


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
            ["ip", "link", "set", "dev", interface_name, "up"], check=True
        )
    except subprocess.CalledProcessError as e:
        logger.error("Failed to configure TAP interface %s: %s", interface_name, e)
        raise TapInterfaceError(
            f"Failed to configure TAP interface {interface_name}: {e}"
        ) from e


def plc_to_tap(plc, tap_fd, stop_event: Optional[threading.Event] = None):
    """Read from the PLC and write to the TAP interface."""
    while stop_event is None or not stop_event.is_set():
        try:
            frame = plc.recv()
        except Exception as exc:  # pragma: no cover - safety
            logger.error("Error receiving frame from PLC: %s", exc)
            break
        if frame:
            os.write(tap_fd, bytes(frame))
            logger.debug("Forwarded frame from PLC to TAP: %s", frame)

    logger.info("plc_to_tap loop terminated")


def tap_to_plc(plc, tap_fd, stop_event: Optional[threading.Event] = None):
    """Read from the TAP interface and write to the PLC."""
    while stop_event is None or not stop_event.is_set():
        try:
            packet = os.read(tap_fd, ETH_FRAME_MAX)
        except Exception as exc:  # pragma: no cover - safety
            logger.error("Error reading packet from TAP: %s", exc)
            break
        if packet:
            plc.send(list(packet))
            logger.debug("Forwarded packet from TAP to PLC: %s", packet)

    logger.info("tap_to_plc loop terminated")


def main():
    logging.basicConfig(level=logging.INFO)

    # Create and configure the TAP interface
    tap_fd, tap_name = create_tap_interface()
    configure_tap_interface(tap_name, "192.168.1.1", "24")

    # Create the PLC network object
    plc = PLCNetwork()

    stop_event = threading.Event()

    # Create and start the threads
    plc_to_tap_thread = threading.Thread(
        target=plc_to_tap, args=(plc, tap_fd, stop_event)
    )
    tap_to_plc_thread = threading.Thread(
        target=tap_to_plc, args=(plc, tap_fd, stop_event)
    )

    plc_to_tap_thread.start()
    tap_to_plc_thread.start()

    logger.info(
        "TAP device '%s' is up and bridged with the PLC modem (frame size %s bytes)",
        tap_name,
        ETH_FRAME_MAX,
    )
    logger.info("Press Ctrl+C to stop.")

    try:
        plc_to_tap_thread.join()
        tap_to_plc_thread.join()
    except KeyboardInterrupt:
        logger.info("Closing TAP device and stopping threads.")
        stop_event.set()
        plc_to_tap_thread.join()
        tap_to_plc_thread.join()
    finally:
        os.close(tap_fd)
        plc.close()

if __name__ == '__main__':
    main()
