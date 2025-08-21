import sys
import pathlib
import threading
import time
from subprocess import CalledProcessError
from unittest.mock import MagicMock, patch
import pytest

# Ensure src path is in sys.path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / 'src'))

from plc_communication.plc_to_tap import (
    ETH_FRAME_MAX,
    TapInterfaceError,
    configure_tap_interface,
    create_tap_interface,
    plc_to_tap,
    tap_to_plc,
)


def test_plc_to_tap_forwards_frames():
    """A frame received from PLC is written to the TAP interface."""
    frame = [0x01, 0x02, 0x03]
    plc = MagicMock()
    plc.recv = MagicMock(side_effect=[frame, StopIteration])

    with patch('plc_communication.plc_to_tap.os.write') as mock_write:
        with pytest.raises(StopIteration):
            plc_to_tap(plc, 1)
    mock_write.assert_called_once_with(1, bytes(frame))


def test_tap_to_plc_forwards_frames():
    """Packets read from TAP are forwarded to the PLC."""
    packet = b'\x01\x02\x03'
    plc = MagicMock()
    plc.send = MagicMock()

    with patch('plc_communication.plc_to_tap.os.read', side_effect=[packet, StopIteration]) as mock_read:
        with pytest.raises(StopIteration):
            tap_to_plc(plc, 2)
    mock_read.assert_called_with(2, ETH_FRAME_MAX)
    plc.send.assert_called_once_with(list(packet))


def test_plc_to_tap_respects_stop_event():
    """Loop terminates when stop event is set."""
    frame = [0x01]
    plc = MagicMock()
    plc.recv = MagicMock(return_value=frame)
    stop_event = threading.Event()

    with patch("plc_communication.plc_to_tap.os.write"):
        thread = threading.Thread(target=plc_to_tap, args=(plc, 1, stop_event))
        thread.start()
        time.sleep(0.01)
        stop_event.set()
        thread.join(timeout=1)
        assert not thread.is_alive()


def test_tap_to_plc_respects_stop_event():
    """Loop terminates when stop event is set."""
    packet = b"\x01"
    plc = MagicMock()
    plc.send = MagicMock()
    stop_event = threading.Event()

    with patch("plc_communication.plc_to_tap.os.read", return_value=packet):
        thread = threading.Thread(target=tap_to_plc, args=(plc, 2, stop_event))
        thread.start()
        time.sleep(0.01)
        stop_event.set()
        thread.join(timeout=1)
        assert not thread.is_alive()


def test_create_tap_interface_raises_custom_exception():
    """Failure to open /dev/net/tun raises TapInterfaceError."""
    with patch("plc_communication.plc_to_tap.os.open", side_effect=OSError("fail")):
        with pytest.raises(TapInterfaceError):
            create_tap_interface()


def test_configure_tap_interface_raises_on_failure():
    """subprocess errors are wrapped in TapInterfaceError."""
    with patch(
        "plc_communication.plc_to_tap.subprocess.run",
        side_effect=CalledProcessError(1, ["ip"]),
    ):
        with pytest.raises(TapInterfaceError):
            configure_tap_interface("tap0", "1.1.1.1", "24")
