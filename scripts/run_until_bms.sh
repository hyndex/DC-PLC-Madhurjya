#!/usr/bin/env bash
set -Eeuo pipefail

# Wrapper that repeatedly launches the EVSE until a BMS snapshot is observed
# in the JSON tee (/tmp/evse_run.jsonl by default). It tries ISO_15118_2 first
# and can optionally try DIN as a fallback when provided with EVSE_ID_DIN_HEX.

RUN_SECS=${RUN_SECS:-180}
ATTEMPTS=${ATTEMPTS:-3}
TEE_JSON=${EVSE_TEE_JSON:-/tmp/evse_run.jsonl}
LOG_FILE=${EVSE_RUN_LOG:-/tmp/evse_session_auto.log}

try_once() {
  echo "[auto] Launch attempt $1/$ATTEMPTS (mode=$2)"
  sudo -n rm -f "$TEE_JSON" 2>/dev/null || true
  : > "$LOG_FILE"
  if [[ "$2" == "iso2" ]]; then
    scripts/start_evse_hal.sh --evse-id "${EVSE_ID}" --port "${ESP_CP_PORT}" --adapter esp-uart >>"$LOG_FILE" 2>&1 &
  else
    scripts/start_evse_hal.sh --evse-id "${EVSE_ID}" --port "${ESP_CP_PORT}" --adapter esp-uart >>"$LOG_FILE" 2>&1 &
  fi
  pid=$!
  # wait a bit for HLC to progress and for the tee to be written
  end=$((SECONDS + RUN_SECS))
  found=0
  while [[ $SECONDS -lt $end ]]; do
    if [[ -s "$TEE_JSON" ]]; then
      if python - << 'PY' "$TEE_JSON"; then
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
  : "${EVSE_ID:?Set EVSE_ID}"
  : "${ESP_CP_PORT:?Set ESP_CP_PORT}"
  export EVSE_TEE_JSON="$TEE_JSON"
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

