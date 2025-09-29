#!/usr/bin/env bash
set -euo pipefail

# Simple local smoke tester for Raspberry Pi (no Docker):
# - Assumes setup_rpi.sh has run and /opt/evse-venv exists

VENV_DIR=${VENV_DIR:-/opt/evse-venv}
HOST=${HOST:-localhost}
PORT=${PORT:-8000}

if [ ! -d "$VENV_DIR" ]; then
  echo "Virtualenv not found at $VENV_DIR. Run setup_rpi.sh first." >&2
  exit 1
fi

source "$VENV_DIR/bin/activate"

echo "Starting API on :$PORT ..."
python -m uvicorn src.ccs_sim.fastapi_app:app --host 0.0.0.0 --port "$PORT" &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT

BASE="http://${HOST}:${PORT}"
echo "Waiting for API at ${BASE} ..."
for i in {1..60}; do
  curl -fsS "${BASE}/hlc/status" >/dev/null && break || true
  sleep 1
done

echo "API is up. Starting short session and polling status..."
curl -fsS -X POST "${BASE}/start_session" \
  -H 'Content-Type: application/json' \
  -d '{"target_voltage": 20, "initial_current": 15, "duration_s": 2}' || true

deadline=$(( $(date +%s) + 12 ))
phase=""
while [ $(date +%s) -lt $deadline ]; do
  phase=$(curl -fsS "${BASE}/status" 2>/dev/null | sed -n 's/.*"phase":"\([A-Z]*\)".*/\1/p')
  echo "phase=${phase}"
  [ "$phase" = "COMPLETE" ] && break
  sleep 1
done

echo "Final status:"
curl -fsS "${BASE}/status" || true
echo
echo "Meter:"
curl -fsS "${BASE}/meter" || true
echo
echo "Done."

