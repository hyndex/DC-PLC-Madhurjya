import sys
import pathlib
from unittest.mock import MagicMock, patch
import pytest

# Ensure src path is in sys.path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / 'src'))

from plc_communication.plc_network import PLCNetwork, ETH_FRAME_MAX


def _make_plc(mock_qca):
    with patch('plc_communication.plc_network.QCA7000', return_value=mock_qca):
        plc = PLCNetwork()
    return plc


def test_send_short_frame_pads_without_mutation():
    frame = [0x01, 0x02]
    original = frame.copy()
    mock_qca = MagicMock()
    plc = _make_plc(mock_qca)

    plc.send(frame)

    assert frame == original
    sent_frame = mock_qca.write_ethernet_frame.call_args[0][0]
    assert len(sent_frame) == 4 + 2 + 2 + 60 + 2
    assert sent_frame[8:10] == original
    assert all(b == 0 for b in sent_frame[10:68])


def test_send_accepts_bytes():
    frame = b"\x01\x02\x03"
    mock_qca = MagicMock()
    plc = _make_plc(mock_qca)

    plc.send(frame)

    sent_frame = mock_qca.write_ethernet_frame.call_args[0][0]
    assert sent_frame[8:11] == [1, 2, 3]


def test_round_trip_integrity():
    frame = [0xAA, 0xBB, 0xCC, 0xDD]
    mock_qca = MagicMock()
    plc = _make_plc(mock_qca)

    plc.send(frame)
    qca_frame = mock_qca.write_ethernet_frame.call_args[0][0]
    mock_qca.read_ethernet_frame.return_value = [0, 0, 0, 0] + qca_frame

    received = plc.recv()
    assert received == frame


def test_send_rejects_oversize_frame():
    frame = [0x00] * (ETH_FRAME_MAX + 1)
    mock_qca = MagicMock()
    plc = _make_plc(mock_qca)

    with pytest.raises(ValueError):
        plc.send(frame)


def test_send_rejects_empty_frame():
    mock_qca = MagicMock()
    plc = _make_plc(mock_qca)

    with pytest.raises(ValueError):
        plc.send([])


def test_close_invokes_qca_close():
    mock_qca = MagicMock()
    plc = _make_plc(mock_qca)
    plc.close()
    mock_qca.close.assert_called_once()
