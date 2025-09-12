#!/usr/bin/env bash
set -euo pipefail

# Simple end-to-end DC module exercise over ESP periph JSON-RPC
# - Discovers modules
# - Arms + closes contactor
# - Ramps voltage from START_V to END_V in STEP_V every DWELL_S seconds
# - Then ramps down back to START_V
# - Leaves output disabled and contactor opened

PORT=${PORT:-/dev/ttyUSB0}
BAUD=${BAUD:-115200}
START_V=${START_V:-50}
END_V=${END_V:-500}
STEP_V=${STEP_V:-10}
DWELL_S=${DWELL_S:-3}
CURRENT_A=${CURRENT_A:-10}

CLI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="${CLI_DIR}/esp_periph_cli.py"

if [[ ! -x "${CLI}" ]]; then
  echo "[ERR] ${CLI} not found or not executable" >&2
  exit 1
fi

echo "[i] Using port=${PORT} baud=${BAUD} range=${START_V}-${END_V} step=${STEP_V}V dwell=${DWELL_S}s current=${CURRENT_A}A"

_cli() {
  local subcmd=$1; shift
  python3 "${CLI}" --port "${PORT}" --baud "${BAUD}" "${subcmd}" "$@"
}

_cleanup() {
  echo "[cleanup] Disabling DC and opening contactor..."
  set +e
  _cli enable 0 >/dev/null 2>&1 || true
  _cli contactor 0 >/dev/null 2>&1 || true
}
trap _cleanup EXIT

echo "[1/8] Ping + Info"
_cli ping || true
_cli info || true

echo "[2/8] Arm contactor"
_cli arm >/dev/null

echo "[3/8] Close contactor"
_cli contactor 1

echo "[4/8] Discover modules"
DISC=$(_cli discover)
echo "[disc] ${DISC}"

echo "[5/8] Set initial targets: V=${START_V}V I=${CURRENT_A}A"
_cli set --v "${START_V}" --i "${CURRENT_A}" >/dev/null

echo "[6/8] Enable DC output"
_cli enable 1

echo "[7/8] Ramp up"
v=${START_V}
while (( $(printf '%.0f' "$v") < END_V )); do
  v=$(python3 - <<PY
v=${v}; step=${STEP_V}
print(f"{v+step:.3f}")
PY
)
  if (( $(python3 - <<PY
v=${v}; end=${END_V}
print(int(v>=end))
PY
) )); then v=${END_V}; fi
  echo "[set] V=${v}V I=${CURRENT_A}A"
  _cli set --v "${v}" --i "${CURRENT_A}" >/dev/null
  sleep "${DWELL_S}"
  RAW=$(_cli status || echo '{}')
  if [[ -x "${CLI_DIR}/esp_status_summary.py" ]]; then
    echo "$RAW" | "${CLI_DIR}/esp_status_summary.py" || echo "$RAW"
  else
    echo "$RAW"
  fi
  if (( $(printf '%.0f' "$v") >= END_V )); then break; fi
done

echo "[7b/8] Ramp down"
v=${END_V}
while (( $(printf '%.0f' "$v") > START_V )); do
  v=$(python3 - <<PY
v=${v}; step=${STEP_V}
nv=v-step
print(f"{nv if nv>0 else 0:.3f}")
PY
)
  if (( $(python3 - <<PY
v=${v}; st=${START_V}
print(int(v<=st))
PY
) )); then v=${START_V}; fi
  echo "[set] V=${v}V I=${CURRENT_A}A"
  _cli set --v "${v}" --i "${CURRENT_A}" >/dev/null
  sleep "${DWELL_S}"
  RAW=$(_cli status || echo '{}')
  if [[ -x "${CLI_DIR}/esp_status_summary.py" ]]; then
    echo "$RAW" | "${CLI_DIR}/esp_status_summary.py" || echo "$RAW"
  else
    echo "$RAW"
  fi
  if (( $(printf '%.0f' "$v") <= START_V )); then break; fi
done

echo "[8/8] Disable DC and open contactor"
_cli enable 0
_cli contactor 0

echo "[done] Module test completed"
