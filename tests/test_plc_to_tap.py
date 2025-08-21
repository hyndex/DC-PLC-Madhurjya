import sys
import pathlib
import threading
import subprocess
from unittest.mock import MagicMock, patch
import pytest

# Ensure src path is in sys.path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / 'src'))

from plc_communication.plc_to_tap import (
    plc_to_tap,
    tap_to_plc,
    ETH_FRAME_MAX,
    create_tap_interface,
    configure_tap_interface,
    TapInterfaceError,
)


def test_plc_to_tap_forwards_frames():
    """A frame received from PLC is written to the TAP interface."""
    frame = [0x01, 0x02, 0x03]
    plc = MagicMock()
    stop_event = threading.Event()

    def recv():
        stop_event.set()
        return frame

    plc.recv = MagicMock(side_effect=recv)

    with patch('plc_communication.plc_to_tap.os.write') as mock_write:
        plc_to_tap(plc, 1, stop_event)
    mock_write.assert_called_once_with(1, bytes(frame))


def test_tap_to_plc_forwards_frames():
    """Packets read from TAP are forwarded to the PLC."""
    packet = b'\x01\x02\x03'
    plc = MagicMock()
    plc.send = MagicMock()
    stop_event = threading.Event()

    def read(fd, size):
        stop_event.set()
        return packet

    with patch('plc_communication.plc_to_tap.os.read', side_effect=read) as mock_read:
        tap_to_plc(plc, 2, stop_event)
    mock_read.assert_called_with(2, ETH_FRAME_MAX)
    plc.send.assert_called_once_with(list(packet))


def test_create_tap_interface_error():
    """Errors creating the TAP device raise TapInterfaceError."""
    with patch('plc_communication.plc_to_tap.os.open', side_effect=OSError('fail')):
        with pytest.raises(TapInterfaceError):
            create_tap_interface()


def test_configure_tap_interface_error():
    """Subprocess failures during configuration raise TapInterfaceError."""
    with patch(
        'plc_communication.plc_to_tap.subprocess.run',
        side_effect=subprocess.CalledProcessError(1, ['ip']),
    ):
        with pytest.raises(TapInterfaceError):
            configure_tap_interface('tap0', '192.168.1.1', '24')
