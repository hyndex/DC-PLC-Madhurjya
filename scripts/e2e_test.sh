#!/usr/bin/env bash
set -euo pipefail

# End-to-end test harness: soft-reset PLC, nudge CP, run HAL orchestrator,
# sniff EV MAC early, then summarize ISO15118 stages and BMS demands.

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
LOG=${LOG:-/tmp/evse_e2e.jsonl}

# Prefer system python if /opt/evse-venv is missing
if [ -x /opt/evse-venv/bin/python ]; then
  PYBIN=/opt/evse-venv/bin/python
else
  PYBIN=$(command -v python3)
fi

# Detect PLC interface (qcaspi) or fall back to eth1
detect_plc_iface() {
  for n in /sys/class/net/*; do
    i=$(basename "$n")
    if ethtool -i "$i" 2>/dev/null | grep -qi '^driver:\s*qcaspi'; then
      echo "$i"; return 0;
    fi
  done
  # Try common names
  if ip link show eth1 >/dev/null 2>&1; then echo eth1; return 0; fi
  if ip link show plc0 >/dev/null 2>&1; then echo plc0; return 0; fi
  echo eth1
}

# Detect ESP serial port (USB or onboard alias)
detect_esp_port() {
  # Prefer Pi's primary UART first because firmware JSON control is on UART, not USB CDC
  for p in /dev/serial0 /dev/ttyAMA0 /dev/ttyACM* /dev/ttyUSB*; do
    [ -e "$p" ] && echo "$p" && return 0
  done
  echo /dev/serial0
}

PLC_IFACE=$(detect_plc_iface)
ESP_PORT=$(detect_esp_port)
echo "[e2e] Using PLC interface: $PLC_IFACE"
echo "[e2e] Using ESP port: $ESP_PORT"

echo "[e2e] Soft-resetting PLC (qcaspi) ..."
if [ "${SKIP_PLC_RESET:-}" != "1" ]; then
  sudo bash "$ROOT_DIR/scripts/plc_soft_reset.sh"
else
  echo "[e2e] SKIP_PLC_RESET=1; skipping soft reset"
fi

echo "[e2e] Ensuring ESP CP in dc mode and issuing SLAC restart hint ..."
sudo -E "$PYBIN" - <<PY
from src.evse_hal.esp_cp_client import EspCpClient
c=EspCpClient('$ESP_PORT')
c.connect()
c.set_mode('dc')
try:
    c.restart_slac_hint(500)
except Exception:
    pass
print('ESP status:', c.get_status(1.0))
c.close()
PY

echo "[e2e] Starting HAL orchestrator (logs -> $LOG) ..."
sudo -E env \
  EVSE_CONTROLLER=hal \
  EVSE_HAL_ADAPTER=esp-periph \
  ESP_PERIPH_PORT="$ESP_PORT" \
  ESP_CP_PORT="$ESP_PORT" \
  EVSE_LOG_LEVEL=INFO \
  EVSE_LOG_FORMAT=json \
  EVSE_LOG_FILE="$LOG" \
  SLAC_MAX_ATTEMPTS=3 \
  SLAC_WAIT_TIMEOUT_S=50 \
  SLAC_RESTART_HINT_MS=400 \
  CP_DEBOUNCE_S=0.10 \
  PYTHONPATH="$ROOT_DIR/src:$ROOT_DIR/src/iso15118:$ROOT_DIR/src/pyslac" \
  "$PYBIN" "$ROOT_DIR/src/evse_main.py" --evse-id EVSE-1 --iface "$PLC_IFACE" &
EVSE_PID=$!
trap 'kill "$EVSE_PID" 2>/dev/null || true' EXIT
sleep 2

echo "[e2e] Sniffing for CM_SLAC_PARM.REQ to capture EV MAC (90s) ..."
set +e
sudo -E env PYTHONPATH="$ROOT_DIR/src/pyslac" "$PYBIN" "$ROOT_DIR/scripts/sniff_ev_mac.py" --iface "$PLC_IFACE" --timeout 90
SNIF_RC=$?
set -e

echo "[e2e] Allowing SECC to progress for 240s ..."
sleep 240 || true

echo "[e2e] Stopping HAL orchestrator ..."
kill "$EVSE_PID" 2>/dev/null || true
sleep 1

echo "[e2e] Summary (EV MAC, ISO stages, BMS demands)"
"$PYBIN" - <<'PY'
import json, os
p=os.environ.get('LOG','/tmp/evse_e2e.jsonl')
if not os.path.exists(p):
    print('[summary] Log file not found:', p)
    raise SystemExit(1)
ev=nid=run=None; stages=[]; bms=[]
for line in open(p):
    try: rec=json.loads(line)
    except: continue
    msg=rec.get('msg','')
    if 'SLAC peer info' in msg:
        ev = rec.get('ev_mac') or ev
        nid = rec.get('nid') or nid
        run = rec.get('run_id') or run
        if not (ev and nid and run):
            s=msg
            if 'ev_mac=' in s: ev = ev or s.split('ev_mac=')[1].split()[0].strip(',')
            if 'nid=' in s:    nid= nid or s.split('nid=')[1].split()[0].strip(',')
            if 'run_id=' in s: run= run or s.split('run_id=')[1].split()[0].strip(',')
    if msg == 'ISO15118 state':
        stages.append(f"{rec.get('ts')} {rec.get('iso_state')}")
        b = rec.get('bms') or {}
        bms.append(f"{rec.get('ts')} Vp={b.get('present_voltage')} Vt={b.get('target_voltage')} It={b.get('target_current')} Imax={b.get('max_current_limit')} EVCC={b.get('evcc_id')}")
print('EV MAC:', ev)
print('NID:', nid)
print('RUN_ID:', run)
print('\nISO Stages:')
print('\n'.join(stages) or '(none)')
print('\nBMS Demands:')
print('\n'.join(bms) or '(none)')
PY

echo "[e2e] Done."
