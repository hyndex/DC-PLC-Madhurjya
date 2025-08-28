#!/usr/bin/env bash
set -euo pipefail

HOST=${1:-localhost}
PORT=${2:-8000}
BASE="http://${HOST}:${PORT}"

echo "Waiting for API at ${BASE} ..."
for i in {1..60}; do
  if curl -fsS "${BASE}/hlc/status" >/dev/null; then
    break
  fi
  sleep 1
done

echo "API is up. Checking status endpoints..."
curl -fsS "${BASE}/status" || true
echo
curl -fsS "${BASE}/hlc/status" || true
echo

echo "Start short session..."
curl -fsS -X POST "${BASE}/start_session" \
  -H 'Content-Type: application/json' \
  -d '{"target_voltage": 20, "initial_current": 15, "duration_s": 2}' || true
echo

echo "Poll status until COMPLETE/ABORTED..."
deadline=$(( $(date +%s) + 10 ))
phase=""
while [ $(date +%s) -lt $deadline ]; do
  if command -v jq >/dev/null 2>&1; then
    phase=$(curl -fsS "${BASE}/status" 2>/dev/null | jq -r .phase || echo "")
  elif command -v python >/dev/null 2>&1; then
    phase=$(curl -fsS "${BASE}/status" 2>/dev/null | python -c 'import sys, json; print(json.loads(sys.stdin.read()).get("phase"))' || echo "")
  else
    # Best-effort fallback without JSON tooling
    phase=$(curl -fsS "${BASE}/status" 2>/dev/null | sed -n 's/.*"phase":"\([A-Z]*\)".*/\1/p')
  fi
  echo "phase=${phase}"
  if [ "${phase}" = "COMPLETE" ] || [ "${phase}" = "ABORTED" ]; then
    break
  fi
  sleep 1
done

echo "Final status:"
curl -fsS "${BASE}/status" || true
echo
echo "Meter:"
curl -fsS "${BASE}/meter" || true
echo

echo "Smoke test completed."
