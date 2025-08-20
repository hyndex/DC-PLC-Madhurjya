import sys
import pathlib
from unittest.mock import MagicMock, patch
import pytest

# Ensure src path is in sys.path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / 'src'))

from plc_communication.plc_to_tap import plc_to_tap, tap_to_plc, ETH_FRAME_MAX


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
