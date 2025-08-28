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

