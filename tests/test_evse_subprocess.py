import os
import sys
import subprocess
import pathlib


def test_evse_main_subprocess(evse_subprocess_script):
    """Integration test launching evse_main in a subprocess."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(repo_root / 'src')
    result = subprocess.run(
        [sys.executable, '-c', evse_subprocess_script],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'SLAC match successful, launching ISO 15118 SECC' in output
    assert 'SECC started on tap0' in output
    assert 'plc_to_tap wrote frame' in output
    assert 'tap_to_plc sent' in output
    assert 'QCA write' in output
