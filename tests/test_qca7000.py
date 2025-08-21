from unittest import TestCase
from unittest import TestCase
from unittest.mock import MagicMock, patch
import pytest

from src.plc_communication.qca7000 import (
    QCA7000,
    SPI_INT_CPU_ON,
    SPI_INT_WRBUF_ERR,
    SPI_INT_RDBUF_ERR,
    SignatureError,
    BufferSpaceError,
)


class QCA7000RecoveryTests(TestCase):
    @patch('src.plc_communication.qca7000.spidev.SpiDev')
    def test_read_ethernet_frame_cpu_on_triggers_initialize(self, mock_spi):
        mock_spi.return_value = MagicMock()
        q = QCA7000()
        q.initialize = MagicMock()
        q.reset_chip = MagicMock()
        q._read_register = MagicMock(side_effect=[SPI_INT_CPU_ON, 0])

        result = q.read_ethernet_frame()

        q.initialize.assert_called_once()
        q.reset_chip.assert_not_called()
        self.assertIsNone(result)

    @patch('src.plc_communication.qca7000.spidev.SpiDev')
    def test_read_ethernet_frame_rdbuf_err_triggers_reset(self, mock_spi):
        mock_spi.return_value = MagicMock()
        q = QCA7000()
        q.initialize = MagicMock()
        q.reset_chip = MagicMock()
        q._read_register = MagicMock(side_effect=[SPI_INT_RDBUF_ERR, 0])

        result = q.read_ethernet_frame()

        q.reset_chip.assert_called_once()
        self.assertIsNone(result)

    @patch('src.plc_communication.qca7000.spidev.SpiDev')
    def test_write_ethernet_frame_wrbuf_err_triggers_reset(self, mock_spi):
        mock_spi.return_value = MagicMock()
        q = QCA7000()
        q.initialize = MagicMock()
        q.reset_chip = MagicMock()
        q._spi_transfer = MagicMock()
        q._write_register = MagicMock()
        q._read_register = MagicMock(side_effect=[SPI_INT_WRBUF_ERR, 0, 1000])

        q.write_ethernet_frame([0x00])

        q.reset_chip.assert_called_once()

    @patch('src.plc_communication.qca7000.spidev.SpiDev')
    def test_write_ethernet_frame_cpu_on_triggers_initialize(self, mock_spi):
        mock_spi.return_value = MagicMock()
        q = QCA7000()
        q.initialize = MagicMock()
        q.reset_chip = MagicMock()
        q._spi_transfer = MagicMock()
        q._write_register = MagicMock()
        q._read_register = MagicMock(side_effect=[SPI_INT_CPU_ON, 0, 1000])

        q.write_ethernet_frame([0x00])

        q.initialize.assert_called_once()
        q.reset_chip.assert_not_called()


@patch('src.plc_communication.qca7000.spidev.SpiDev')
def test_initialize_invalid_signature_raises_signature_error(mock_spi):
    mock_spi.return_value = MagicMock()
    q = QCA7000()
    q._read_register = MagicMock(side_effect=[0, 0x1234])
    with pytest.raises(SignatureError):
        q.initialize()
    q.close()


@patch('src.plc_communication.qca7000.spidev.SpiDev')
def test_write_ethernet_frame_buffer_space_error(mock_spi):
    mock_spi.return_value = MagicMock()
    q = QCA7000()
    q._check_and_handle_interrupts = MagicMock()
    q._read_register = MagicMock(return_value=1)
    with pytest.raises(BufferSpaceError):
        q.write_ethernet_frame([0x00, 0x01])
    q.close()


@patch('src.plc_communication.qca7000.spidev.SpiDev')
def test_close_closes_spi(mock_spi):
    spi_instance = mock_spi.return_value
    q = QCA7000()
    q.close()
    spi_instance.close.assert_called_once()

