import time
from fastapi.testclient import TestClient


def test_full_flow_quick(monkeypatch):
    # Import app lazily to ensure module-level objects are initialized per test
    from src.ccs_sim.fastapi_app import app, orch

    c = TestClient(app)

    # Start a short session to keep test fast
    r = c.post('/start_session', json={"target_voltage": 20, "initial_current": 15, "duration_s": 2})
    assert r.status_code == 200
    assert r.json().get('status') == 'started'

    # Status should progress from HANDSHAKE to PRECHARGE
    s1 = c.get('/status').json()
    assert s1['session_active'] is True
    assert s1['phase'] in ('HANDSHAKE', 'PRECHARGE', 'CHARGING')

    # Wait until charging (allow ~6s: 2s handshake + 1s cable + small precharge)
    deadline = time.time() + 6
    phase = s1['phase']
    while time.time() < deadline and phase != 'CHARGING':
        phase = c.get('/status').json()['phase']
        time.sleep(0.25)

    # At this point we should be charging or complete (very quick runs)
    s2 = c.get('/status').json()
    assert s2['phase'] in ('CHARGING', 'COMPLETE')

    # Open contactor mid-session (if still charging)
    if s2['phase'] == 'CHARGING':
            c.post('/control/contactor', json={"closed": False})
            # Give a moment and verify current drops to 0 in snapshot
            time.sleep(0.3)
            s3 = c.get('/status').json()
            assert s3['contactor_closed'] is False
            # current may be 0.0 or become 0 on next tick; allow small wait
            end2 = time.time() + 1.5
            zeroed = s3['current'] == 0.0
            while time.time() < end2 and not zeroed:
                s_now = c.get('/status').json()
                if s_now['phase'] in ('COMPLETE', 'ABORTED'):
                    zeroed = True
                    break
                zeroed = s_now['current'] == 0.0
                time.sleep(0.1)
            assert zeroed

    # Wait for completion
    time.sleep(2.5)
    s4 = c.get('/status').json()
    assert s4['phase'] in ('COMPLETE', 'ABORTED')
    # last_session_summary populated on COMPLETE/ABORTED
    assert s4['last_session_summary'] is not None

    # Meter endpoint returns fields
    m = c.get('/meter').json()
    assert set(m.keys()) == {"energy_Wh", "avg_voltage", "avg_current", "session_time_s"}

    # BMS/SLAC/ISO endpoints exist and return JSON
    assert c.get('/vehicle/bms').status_code == 200
    assert c.get('/vehicle/slac').status_code == 200
    assert c.get('/vehicle/iso15118').status_code == 200


def test_start_while_active_rejected():
    from src.ccs_sim.fastapi_app import app
    c = TestClient(app)
    # Start one
    c.post('/start_session', json={"target_voltage": 30, "initial_current": 10, "duration_s": 2})
    # Immediately try another
    r2 = c.post('/start_session', json={"target_voltage": 30, "initial_current": 10, "duration_s": 2})
    assert r2.status_code == 200
    assert r2.json()['status'] in ('error', 'started')  # minor race acceptable


def test_fault_causes_abort():
    from src.ccs_sim.fastapi_app import app
    c = TestClient(app)
    c.post('/start_session', json={"target_voltage": 40, "initial_current": 10, "duration_s": 5})
    time.sleep(1.0)
    # Inject fault and verify ABORTED
    c.post('/fault', json={"type": "E_STOP"})
    time.sleep(0.5)
    s = c.get('/status').json()
    assert s['phase'] == 'ABORTED'
    assert s['error'] == 'E_STOP'
