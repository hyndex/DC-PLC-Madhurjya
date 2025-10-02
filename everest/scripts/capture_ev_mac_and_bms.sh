#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OUT_DIR="${OUT_DIR:-$ROOT}" 
EV_MAC_FILE="$OUT_DIR/ev_mac.txt"
BMS_RAW_FILE="$OUT_DIR/bms_raw.ndjson"
REPORT_JSON="$OUT_DIR/session_report.json"

echo "[capture] Preparing MQTT capture and restarting Everest service" >&2
rm -f "$EV_MAC_FILE" "$BMS_RAW_FILE" "$REPORT_JSON" "$OUT_DIR/mqtt_all.ndjson"

# Start a broad MQTT capture first to not miss early publishes
DISC_SECS="${WAIT_DISC:-30}"
echo "[capture] Subscribing everest/# for ${DISC_SECS}s (background)" >&2
timeout "$DISC_SECS"s mosquitto_sub -v -t 'everest/#' > "$OUT_DIR/mqtt_all.ndjson" &
CAP_PID=$!

# Restart the stack while capture is already running
sudo systemctl restart everest-hw || true
sleep 1

# Derive topics more robustly by subscribing directly to patterns
SLAC_TOPIC_PATTERN="everest/+/slac/ev_mac_address"
V2G_TOPIC_PATTERN="everest/+/evse_v2g/#"

echo "[capture] Waiting for EV MAC from ${SLAC_TOPIC_PATTERN} (up to ${WAIT_MAC:-120}s)" >&2
timeout "${WAIT_MAC:-120}s" mosquitto_sub -v -t "$SLAC_TOPIC_PATTERN" |
  grep -Eo '[A-F0-9]{2}(:[A-F0-9]{2}){5}' |
  head -n1 > "$EV_MAC_FILE" || true

if [[ -s "$EV_MAC_FILE" ]]; then
  EV_MAC=$(cat "$EV_MAC_FILE")
  echo "[capture] EV MAC: $EV_MAC" >&2
else
  echo "[capture] EV MAC not observed yet. Continuing to capture BMS params..." >&2
fi

echo "[capture] Collecting HLC/BMS messages for ${WAIT_BMS:-180}s from ${V2G_TOPIC_PATTERN}" >&2
timeout "${WAIT_BMS:-180}s" mosquitto_sub -v -t "$V2G_TOPIC_PATTERN" > "$BMS_RAW_FILE" || true

# Ensure the broad discovery capture completes
wait "$CAP_PID" 2>/dev/null || true

echo "[capture] Building JSON report" >&2
jq -s --arg evmac "${EV_MAC:-}" '
  {
    ev_mac: ($evmac | if .=="" then null else . end),
    bms: {
      samples: map(select(.!=null))
    }
  }
' <(awk '{sub(/^[^ ]+ /,""); print}' "$BMS_RAW_FILE" | sed -n 's/.*\({.*}\).*/\1/p' | jq -c . 2>/dev/null) > "$REPORT_JSON" || true

if [[ -s "$REPORT_JSON" ]]; then
  echo "[capture] Report written: $REPORT_JSON" >&2
else
  echo "[capture] No BMS JSON detected in MQTT payloads." >&2
fi
