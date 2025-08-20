import sys
import pathlib
from unittest import TestCase
from unittest.mock import MagicMock, patch

# Ensure src path is in sys.path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / 'src'))

from plc_communication.qca7000 import (
    QCA7000,
    SPI_INT_CPU_ON,
    SPI_INT_WRBUF_ERR,
    SPI_INT_RDBUF_ERR,
)


class QCA7000RecoveryTests(TestCase):
    @patch('plc_communication.qca7000.spidev.SpiDev')
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

    @patch('plc_communication.qca7000.spidev.SpiDev')
    def test_read_ethernet_frame_rdbuf_err_triggers_reset(self, mock_spi):
        mock_spi.return_value = MagicMock()
        q = QCA7000()
        q.initialize = MagicMock()
        q.reset_chip = MagicMock()
        q._read_register = MagicMock(side_effect=[SPI_INT_RDBUF_ERR, 0])

        result = q.read_ethernet_frame()

        q.reset_chip.assert_called_once()
        self.assertIsNone(result)

    @patch('plc_communication.qca7000.spidev.SpiDev')
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

    @patch('plc_communication.qca7000.spidev.SpiDev')
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

