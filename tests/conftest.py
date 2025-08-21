import pytest, textwrap

SUBPROCESS_SCRIPT = r"""
import sys, os, asyncio, logging, types, pty, queue

# Ensure repository src directory is in sys.path
sys.path.insert(0, 'src')

# Create virtual TAP interface using a PTY
master_fd, slave_fd = pty.openpty()

# Patch PLC to TAP bridging functions
import plc_communication.plc_to_tap as ptp

def create_tap_interface():
    return slave_fd, 'tap0'

def configure_tap_interface(name, ip, netmask):
    pass

def plc_to_tap(plc, tap_fd, stop_event=None):
    frame = plc.recv()
    if frame:
        os.write(tap_fd, bytes(frame))
        print('plc_to_tap wrote frame')

def tap_to_plc(plc, tap_fd, stop_event=None):
    plc.send([0x10, 0x20, 0x30])
    print('tap_to_plc sent')

ptp.create_tap_interface = create_tap_interface
ptp.configure_tap_interface = configure_tap_interface
ptp.plc_to_tap = plc_to_tap
ptp.tap_to_plc = tap_to_plc

# Mocked QCA7000 implementation
class MockQCA7000:
    read_q = queue.Queue()
    def __init__(self, *args, **kwargs):
        self.read_q = MockQCA7000.read_q
    def initialize(self):
        pass
    def close(self):
        pass
    def write_ethernet_frame(self, frame):
        print('QCA write', frame[:5])
    def read_ethernet_frame(self):
        try:
            return self.read_q.get_nowait()
        except queue.Empty:
            return None

payload = [1, 2, 3]
frame_len = len(payload)
qca_frame = [0, 0, 0, 0] + [0xAA]*4 + list(frame_len.to_bytes(2, 'little')) + [0, 0] + payload + [0x55, 0x55]
MockQCA7000.read_q.put(qca_frame)

import plc_communication.plc_network as pn
pn.QCA7000 = MockQCA7000

# Stub pyslac package
class SlacConfig:
    def load_envs(self, path):
        pass

class SlacEvseSession:
    def __init__(self, evse_id, iface, cfg):
        self.iface = iface
        self.state = None
    async def evse_set_key(self):
        pass

STATE_MATCHED = 'MATCHED'

class SlacSessionController:
    async def process_cp_state(self, session, state):
        if state == 'C':
            session.state = STATE_MATCHED

sys.modules['pyslac'] = types.ModuleType('pyslac')
sys.modules['pyslac.environment'] = types.ModuleType('pyslac.environment')
sys.modules['pyslac.environment'].Config = SlacConfig
sys.modules['pyslac.session'] = types.ModuleType('pyslac.session')
sys.modules['pyslac.session'].SlacEvseSession = SlacEvseSession
sys.modules['pyslac.session'].SlacSessionController = SlacSessionController
sys.modules['pyslac.session'].STATE_MATCHED = STATE_MATCHED

# Stub iso15118 package
class SeccConfig:
    def load_envs(self, path):
        pass
    def print_settings(self):
        pass

class SimEVSEController:
    async def set_status(self, status):
        pass

class ServiceStatus:
    STARTING = 'STARTING'

class ExificientEXICodec:
    pass

class SECCHandler:
    def __init__(self, **kwargs):
        pass
    async def start(self, iface):
        print('SECC started on', iface)

sys.modules['iso15118'] = types.ModuleType('iso15118')
sys.modules['iso15118.secc'] = types.ModuleType('iso15118.secc')
sys.modules['iso15118.secc.secc_settings'] = types.ModuleType('iso15118.secc.secc_settings')
sys.modules['iso15118.secc.secc_settings'].Config = SeccConfig
sys.modules['iso15118.secc.controller'] = types.ModuleType('iso15118.secc.controller')
sys.modules['iso15118.secc.controller.simulator'] = types.ModuleType('iso15118.secc.controller.simulator')
sys.modules['iso15118.secc.controller.simulator'].SimEVSEController = SimEVSEController
sys.modules['iso15118.secc.controller.interface'] = types.ModuleType('iso15118.secc.controller.interface')
sys.modules['iso15118.secc.controller.interface'].ServiceStatus = ServiceStatus
sys.modules['iso15118.shared'] = types.ModuleType('iso15118.shared')
sys.modules['iso15118.shared.exi_codec'] = types.ModuleType('iso15118.shared.exi_codec')
sys.modules['iso15118.shared.exi_codec'].ExificientEXICodec = ExificientEXICodec
sys.modules['iso15118.secc'].SECCHandler = SECCHandler

# Speed up asyncio sleeps
async def fast_sleep(_):
    pass
asyncio.sleep = fast_sleep

import evse_main
sys.argv = ['evse_main.py', '--evse-id', 'TEST']
logging.basicConfig(level=logging.INFO)
evse_main.main()
import time
time.sleep(0.1)
"""


@pytest.fixture
def evse_subprocess_script():
    """Provide the script used to run evse_main in a subprocess with mocked deps."""
    # Verify PTY support; skip if unavailable
    try:
        import pty
        pty.openpty()
    except Exception:
        pytest.skip('PTY devices unavailable')
    return textwrap.dedent(SUBPROCESS_SCRIPT)
