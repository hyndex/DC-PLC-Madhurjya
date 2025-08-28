from fastapi.testclient import TestClient


def test_hlc_endpoints_existence():
    from src.ccs_sim.fastapi_app import app

    c = TestClient(app)
    # Initial status
    s = c.get('/hlc/status').json()
    assert 'state' in s and s['state'] in ('stopped', 'starting', 'running', 'error')

    # Start HLC (may error in this environment; ensure endpoint responds)
    r = c.post('/hlc/start', json={"iface": "eth0"})
    assert r.status_code == 200
    st = r.json().get('status', {})
    assert 'state' in st

    # Stop HLC
    r2 = c.post('/hlc/stop')
    assert r2.status_code == 200
    st2 = r2.json().get('status', {})
    assert 'state' in st2


def test_slac_glue_triggers_hlc_start():
    from src.ccs_sim.fastapi_app import app
    c = TestClient(app)
    # Start matching and then matched
    r1 = c.post('/slac/start_matching')
    assert r1.status_code == 200
    assert r1.json()['state'] == 'MATCHING'
    r2 = c.post('/slac/matched', json={"ev_mac": "AA:BB:CC:DD:EE:FF", "iface": "eth0"})
    assert r2.status_code == 200
    body = r2.json()
    assert 'slac' in body and 'hlc' in body
    assert body['slac']['state'] == 'MATCHED'
    assert 'state' in body['hlc']
