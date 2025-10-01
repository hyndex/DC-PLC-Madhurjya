#!/usr/bin/env bash
set -euo pipefail

# Simple watchdog that checks PLC stats and triggers a soft reset on issues.
# Requires: qca_health.sh and plc_soft_reset.sh in the same directory.

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)

HEALTH_OK_PATTERN="link:up"
SLEEP_OK=${SLEEP_OK:-10}
SLEEP_FAIL=${SLEEP_FAIL:-5}
MAX_FAILS=${MAX_FAILS:-3}

consec_fail=0
echo "[qca_watchdog] Started (ok_sleep=${SLEEP_OK}s, fail_sleep=${SLEEP_FAIL}s, max_fails=${MAX_FAILS})" >&2
while true; do
  out="$(${SCRIPT_DIR}/qca_health.sh || true)"
  if echo "$out" | grep -qi "$HEALTH_OK_PATTERN"; then
    consec_fail=0
    sleep "$SLEEP_OK"
  else
    consec_fail=$((consec_fail+1))
    echo "[qca_watchdog] Health check failed ($consec_fail): $(echo "$out" | tr '\n' ' ')" >&2
    if (( consec_fail >= MAX_FAILS )); then
      echo "[qca_watchdog] Triggering PLC soft reset" >&2
      QCASPI_CLKSPEED="${EVSE_PLC_AUTO_SOFT_RESET_SPEED:-8000000}" \
      QCASPI_BURST="${EVSE_PLC_AUTO_SOFT_RESET_BURST:-5000}" \
      QCASPI_PLUGGABLE="${QCASPI_PLUGGABLE:-1}" \
      bash "${SCRIPT_DIR}/plc_soft_reset.sh" || true
      consec_fail=0
      sleep 2
    else
      sleep "$SLEEP_FAIL"
    fi
  fi
done

