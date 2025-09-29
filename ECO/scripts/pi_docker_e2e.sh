#!/usr/bin/env bash
set -euo pipefail

# End-to-end on Raspberry Pi OS using Docker (no cross-build required)
# - Builds test stage and runs tests during build
# - Builds runtime image
# - Runs the API container on host network
# - Performs a smoke test against the live API

IMAGE_BASE=${1:-eco-rpi0}

echo "[pi-e2e] Checking Docker..."
docker version >/dev/null

echo "[pi-e2e] Building test stage (native arch)..."
docker build \
  --target test \
  -t "${IMAGE_BASE}:test" \
  -f docker/Dockerfile.rpi0 \
  .

echo "[pi-e2e] Building runtime image (native arch)..."
docker build \
  -t "${IMAGE_BASE}:latest" \
  -f docker/Dockerfile.rpi0 \
  .

echo "[pi-e2e] Running container on host network (port 8000)..."
docker rm -f "${IMAGE_BASE}-run" >/dev/null 2>&1 || true
docker run --name "${IMAGE_BASE}-run" \
  --network host \
  -d \
  "${IMAGE_BASE}:latest"

echo "[pi-e2e] Waiting for API to respond..."
BASE=http://127.0.0.1:8000
for i in {1..60}; do
  curl -fsS "$BASE/hlc/status" >/dev/null && break || true
  sleep 1
done

echo "[pi-e2e] Starting short session..."
curl -fsS -X POST "$BASE/start_session" \
  -H 'Content-Type: application/json' \
  -d '{"target_voltage": 20, "initial_current": 15, "duration_s": 2}' || true

echo "[pi-e2e] Polling status..."
deadline=$(( $(date +%s) + 12 ))
phase=""
while [ $(date +%s) -lt $deadline ]; do
  if command -v jq >/dev/null 2>&1; then
    phase=$(curl -fsS "$BASE/status" | jq -r .phase)
  else
    phase=$(curl -fsS "$BASE/status" | sed -n 's/.*"phase":"\([A-Z]*\)".*/\1/p')
  fi
  echo "phase=$phase"
  [ "$phase" = "COMPLETE" ] && break
  sleep 1
done

echo "[pi-e2e] Final status:"
curl -fsS "$BASE/status" || true
echo
echo "[pi-e2e] Meter:"
curl -fsS "$BASE/meter" || true
echo

echo "[pi-e2e] Cleaning up container..."
docker rm -f "${IMAGE_BASE}-run" >/dev/null 2>&1 || true
echo "[pi-e2e] Done."

