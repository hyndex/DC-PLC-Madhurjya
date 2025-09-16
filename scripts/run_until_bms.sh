#!/usr/bin/env bash
set -Eeuo pipefail

# Wrapper that repeatedly launches the EVSE until a BMS snapshot is observed
# in the JSON tee (/tmp/evse_run.jsonl by default). It tries ISO_15118_2 first
# and can optionally try DIN as a fallback when provided with EVSE_ID_DIN_HEX.
#
# Added: pass-through of adapter/iface/port for real-hardware runs.
#
# Usage:
#   scripts/run_until_bms.sh [--evse-id EVSE-1] [--iface eth1] [--port /dev/ttyACM0] \
#       [--adapter esp-periph|esp-uart] [--json /tmp/evse_run.jsonl] \
#       [--attempts 3] [--run-secs 180]
#
# Env overrides:
#   EVSE_ID, PLC_IFACE, ESP_CP_PORT, EVSE_HAL_ADAPTER, EVSE_TEE_JSON,
#   ATTEMPTS, RUN_SECS

RUN_SECS=${RUN_SECS:-180}
ATTEMPTS=${ATTEMPTS:-3}
TEE_JSON=${EVSE_TEE_JSON:-/tmp/evse_run.jsonl}
LOG_FILE=${EVSE_RUN_LOG:-/tmp/evse_session_auto.log}

EVSE_ID=${EVSE_ID:-EVSE-1}
ADAPTER=${EVSE_HAL_ADAPTER:-esp-uart}
IFACE=${PLC_IFACE:-}
PORT=${ESP_CP_PORT:-}

usage() {
  cat <<EOF
Usage: $0 [--evse-id EVSE-1] [--iface IFACE] [--port /dev/ttyACM0] [--adapter esp-periph|esp-uart] [--json FILE] [--attempts N] [--run-secs S]

Examples:
  $0 --evse-id INJPSE0006360 --iface eth1 --port /dev/ttyACM0 --adapter esp-periph
  EVSE_TEE_JSON=/tmp/evse_e2e.jsonl $0 --iface eth1 --port /dev/ttyACM0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0;;
    --evse-id) shift; EVSE_ID=${1:-$EVSE_ID};;
    --iface) shift; IFACE=${1:-$IFACE};;
    --port) shift; PORT=${1:-$PORT};;
    --adapter) shift; ADAPTER=${1:-$ADAPTER};;
    --json) shift; TEE_JSON=${1:-$TEE_JSON};;
    --attempts) shift; ATTEMPTS=${1:-$ATTEMPTS};;
    --run-secs) shift; RUN_SECS=${1:-$RUN_SECS};;
    --) shift; break;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
  shift || true
done

try_once() {
  echo "[auto] Launch attempt $1/$ATTEMPTS (mode=$2)"
  sudo -n rm -f "$TEE_JSON" 2>/dev/null || true
  : > "$LOG_FILE"
  if [[ "$2" == "iso2" ]]; then
    CMD=( scripts/start_evse_hal.sh --evse-id "${EVSE_ID}" --adapter "${ADAPTER}" )
  else
    CMD=( scripts/start_evse_hal.sh --evse-id "${EVSE_ID}" --adapter "${ADAPTER}" )
  fi
  [[ -n "${IFACE}" ]] && CMD+=( --iface "${IFACE}" )
  [[ -n "${PORT}" ]] && CMD+=( --port "${PORT}" )
  echo "[auto] Exec: ${CMD[*]} (TEE_JSON=$TEE_JSON)" >>"$LOG_FILE"
  scripts/start_evse_hal.sh --evse-id "${EVSE_ID}" ${IFACE:+--iface "$IFACE"} ${PORT:+--port "$PORT"} --adapter "${ADAPTER}" >>"$LOG_FILE" 2>&1 &
  pid=$!
  # wait a bit for HLC to progress and for the tee to be written
  end=$((SECONDS + RUN_SECS))
  found=0
  while [[ $SECONDS -lt $end ]]; do
    if [[ -s "$TEE_JSON" ]]; then
      if python - "$TEE_JSON" << 'PY'
import sys, json, pathlib
p = pathlib.Path(sys.argv[1])
for line in p.read_text().splitlines():
    try:
        obj = json.loads(line)
    except Exception:
        continue
    if obj.get('name')=='hlc' and isinstance(obj.get('bms'), dict):
        print(json.dumps({'bms': obj['bms'], 'evse': obj.get('evse'), 'iso_state': obj.get('iso_state')}))
        raise SystemExit(0)
raise SystemExit(1)
PY
      then
        found=1; break
      fi
    fi
    sleep 1
  done
  # stop process
  sudo -n pkill -f "python .* -m src.evse_main" 2>/dev/null || true
  if [[ $found -eq 1 ]]; then
    echo "[auto] BMS snapshot observed."
    return 0
  fi
  echo "[auto] No BMS snapshot this attempt."
  return 1
}

main() {
  : "${EVSE_ID:?Set EVSE_ID or use --evse-id}"
  # PORT optional for esp-periph when single UART; recommended when using esp-uart
  export EVSE_TEE_JSON="$TEE_JSON"
  echo "[auto] EVSE_ID=$EVSE_ID IFACE=${IFACE:-auto} PORT=${PORT:-unset} ADAPTER=$ADAPTER LOG=$LOG_FILE TEE=$TEE_JSON"
  # ISO first
  for i in $(seq 1 "$ATTEMPTS"); do
    if try_once "$i" iso2; then exit 0; fi
  done
  # Optional DIN fallback when EVSE_ID_DIN_HEX provided
  if [[ -n "${EVSE_ID_DIN_HEX:-}" ]]; then
    export SECC_CONFIG_PATH="${SECC_CONFIG_PATH:-$PWD/secc_din.env}"
    for i in $(seq 1 "$ATTEMPTS"); do
      if try_once "$i" din; then exit 0; fi
    done
  fi
  echo "[auto] Exhausted attempts without BMS snapshot. See $LOG_FILE and $TEE_JSON."
  exit 1
}

main "$@"
