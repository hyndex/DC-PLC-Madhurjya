import sys
import types
import pathlib
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Stub external dependencies before importing the module under test
pyslac_env = types.ModuleType('pyslac.environment')
pyslac_env.Config = type('SlacConfig', (), {})

pyslac_session = types.ModuleType('pyslac.session')
class _SlacSessionController:
    pass
pyslac_session.SlacSessionController = _SlacSessionController
pyslac_session.SlacEvseSession = object
pyslac_session.STATE_MATCHED = 'MATCHED'

sys.modules['pyslac'] = types.ModuleType('pyslac')
sys.modules['pyslac.environment'] = pyslac_env
sys.modules['pyslac.session'] = pyslac_session

iso_secc_settings = types.ModuleType('iso15118.secc.secc_settings')
class _SeccConfig:
    def load_envs(self, path):
        pass
    def print_settings(self):
        pass
iso_secc_settings.Config = _SeccConfig

iso_controller_sim = types.ModuleType('iso15118.secc.controller.simulator')
class _SimEVSEController:
    async def set_status(self, status):
        pass
iso_controller_sim.SimEVSEController = _SimEVSEController

iso_controller_interface = types.ModuleType('iso15118.secc.controller.interface')
class _ServiceStatus:
    STARTING = 'STARTING'
iso_controller_interface.ServiceStatus = _ServiceStatus

iso_shared_exi = types.ModuleType('iso15118.shared.exi_codec')
class _ExiCodec:
    pass
iso_shared_exi.ExificientEXICodec = _ExiCodec

iso_secc = types.ModuleType('iso15118.secc')
class _SECCHandler:
    def __init__(self, **kwargs):
        pass
    async def start(self, iface):
        pass
iso_secc.SECCHandler = _SECCHandler

sys.modules['iso15118'] = types.ModuleType('iso15118')
sys.modules['iso15118.secc'] = iso_secc
sys.modules['iso15118.secc.secc_settings'] = iso_secc_settings
sys.modules['iso15118.secc.controller'] = types.ModuleType('iso15118.secc.controller')
sys.modules['iso15118.secc.controller.simulator'] = iso_controller_sim
sys.modules['iso15118.secc.controller.interface'] = iso_controller_interface
sys.modules['iso15118.shared'] = types.ModuleType('iso15118.shared')
sys.modules['iso15118.shared.exi_codec'] = iso_shared_exi

# Ensure src path is in sys.path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / 'src'))

from evse_main import EVSECommunicationController
from pyslac.session import STATE_MATCHED


def test_slac_match_triggers_iso15118_startup():
    """A successful SLAC match should start the ISO 15118 stack."""
    slac_config = MagicMock()
    controller = EVSECommunicationController(
        slac_config=slac_config,
        secc_config_path='conf',
        certificate_store='store',
    )

    session = MagicMock()
    session.evse_set_key = AsyncMock()
    session.iface = 'tap0'
    session.state = STATE_MATCHED
    controller.process_cp_state = AsyncMock()

    async def run_test():
        with patch('evse_main.SlacEvseSession', return_value=session), \
             patch('evse_main.start_secc', new=AsyncMock()) as mock_start_secc, \
             patch('evse_main.asyncio.sleep', new=AsyncMock()):
            await controller.start('evse-1', 'tap0')
            mock_start_secc.assert_awaited_once_with('tap0', 'conf', 'store')

    asyncio.run(run_test())
