#!/usr/bin/env bash
set -Eeuo pipefail

# Start EVSE in HAL mode with sensible defaults and live terminal logs.
# - Detects PLC interface and ESP CP serial port when not provided
# - Preserves env when escalating to root
# - Optional JSON tee to file when EVSE_TEE_JSON is set (path)

# --- Helpers ---
find_iface() {
  if [[ -n "${PLC_IFACE:-}" ]]; then echo "${PLC_IFACE}"; return; fi
  if ip link show plc0 >/dev/null 2>&1; then echo plc0; return; fi
  if ip link show eth1 >/dev/null 2>&1; then echo eth1; return; fi
  if ip link show eth0 >/dev/null 2>&1; then echo eth0; return; fi
  # Fallback: first non-loopback
  ip -o link | awk -F: '{print $2}' | sed 's/ //g' | grep -v '^lo$' | head -n1
}

find_port() {
  if [[ -n "${ESP_CP_PORT:-}" && -e "${ESP_CP_PORT}" ]]; then echo "${ESP_CP_PORT}"; return; fi
  # Prefer USB CDC first (firmware can speak JSON over USB), then USB-UART dongles, then Pi UART
  for p in /dev/ttyACM0 /dev/ttyUSB0 /dev/serial0; do
    if [[ -e "$p" ]]; then echo "$p"; return; fi
  done
  # empty
  echo ""
}

find_python() {
  if [[ -n "${PYTHON:-}" ]]; then echo "${PYTHON}"; return; fi
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then echo "${VIRTUAL_ENV}/bin/python"; return; fi
  if [[ -x "/opt/evse-venv/bin/python" ]]; then echo "/opt/evse-venv/bin/python"; return; fi
  command -v python3 >/dev/null 2>&1 && { command -v python3; return; }
  command -v python >/dev/null 2>&1 && { command -v python; return; }
  echo "python3"
}

usage() {
  cat <<EOF
Usage: $0 [--evse-id EVSE-1] [--iface IFACE] [--port /dev/serial0] [--adapter esp-uart] [--json [FILE]]

Environment overrides:
  EVSE_ID           EVSE identifier (default: EVSE-1)
  PLC_IFACE         PLC netdev (auto-detected if unset)
  ESP_CP_PORT       ESP32-S3 CP UART (auto-detected if unset)
  EVSE_LOG_LEVEL    DEBUG|INFO|... (default: DEBUG)
  EVSE_LOG_FORMAT   text|json (default: text; json forced when --json used)
  EVSE_TEE_JSON     Path to tee JSON logs while showing live text (default: unset)
  EVSE_HAL_ADAPTER  HAL adapter (default: esp-uart)
  SECC_CONFIG_PATH  Path to SECC .env (optional)
  SLAC_CONFIG_PATH  Path to PySLAC .env (optional)
  CERT_STORE_PATH   Path to certificates (PKI_PATH) (optional)

Examples:
  $0 --evse-id EVSE-1            # auto-detect iface/port, text logs
  EVSE_TEE_JSON=/tmp/evse_e2e.jsonl $0 --evse-id EVSE-1  # text in terminal, JSON tee to file
EOF
}

# --- Parse minimal flags ---
EVSE_ID_DEFAULT="${EVSE_ID:-EVSE-1}"
ADAPTER_DEFAULT="${EVSE_HAL_ADAPTER:-esp-uart}"
IFACE_ARG=""
PORT_ARG=""
ADAPTER_ARG="${ADAPTER_DEFAULT}"
TEE_JSON_ARG="${EVSE_TEE_JSON:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0;;
    --evse-id) shift; EVSE_ID_DEFAULT="${1:-$EVSE_ID_DEFAULT}";;
    --iface) shift; IFACE_ARG="${1:-}";;
    --port) shift; PORT_ARG="${1:-}";;
    --adapter) shift; ADAPTER_ARG="${1:-$ADAPTER_ARG}";;
    --json)
      # Optional file path follows; if next token not starting with '-', treat as path
      if [[ ${2:-} != -* && -n ${2:-} ]]; then TEE_JSON_ARG="$2"; shift; else TEE_JSON_ARG="/tmp/evse_e2e.jsonl"; fi
      ;;
    --) shift; break;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2;;
  esac
  shift || true
done

EVSE_ID="${EVSE_ID_DEFAULT}"
IFACE="${IFACE_ARG:-$(find_iface)}"
ESP_PORT="${PORT_ARG:-$(find_port)}"
PY_BIN="$(find_python)"

export EVSE_CONTROLLER="${EVSE_CONTROLLER:-hal}"
export EVSE_HAL_ADAPTER="${ADAPTER_ARG}"
export EVSE_LOG_LEVEL="${EVSE_LOG_LEVEL:-DEBUG}"
export EVSE_LOG_FORMAT="${EVSE_LOG_FORMAT:-text}"

# Optional cert/config envs
ARGS=( -m src.evse_main --evse-id "${EVSE_ID}" --iface "${IFACE}" --controller hal )
[[ -n "${SECC_CONFIG_PATH:-}" ]] && ARGS+=( --secc-config "${SECC_CONFIG_PATH}" )
[[ -n "${SLAC_CONFIG_PATH:-}" ]] && ARGS+=( --slac-config "${SLAC_CONFIG_PATH}" )
[[ -n "${CERT_STORE_PATH:-}" ]] && ARGS+=( --cert-store "${CERT_STORE_PATH}" )

# Prepare env for child
CHILD_ENV=(
  "EVSE_CONTROLLER=${EVSE_CONTROLLER}"
  "EVSE_HAL_ADAPTER=${EVSE_HAL_ADAPTER}"
  "EVSE_LOG_LEVEL=${EVSE_LOG_LEVEL}"
  "EVSE_LOG_FORMAT=${EVSE_LOG_FORMAT}"
)
[[ -n "${ESP_PORT}" ]] && CHILD_ENV+=("ESP_CP_PORT=${ESP_PORT}")
[[ -n "${CERT_STORE_PATH:-}" ]] && CHILD_ENV+=("PKI_PATH=${CERT_STORE_PATH}")
[[ -n "${TEE_JSON_ARG}" ]] && CHILD_ENV+=("EVSE_LOG_JSON_TEE=${TEE_JSON_ARG}")

# Prefer local PySLAC without install
PYTHONPATH_LOCAL="${PYTHONPATH:-}:$PWD/src:$PWD/src/pyslac"

echo "[start-evse-hal] EVSE_ID=${EVSE_ID} IFACE=${IFACE} ADAPTER=${EVSE_HAL_ADAPTER} PORT=${ESP_PORT:-none}"
echo "[start-evse-hal] LOG_LEVEL=${EVSE_LOG_LEVEL} LOG_FORMAT=${EVSE_LOG_FORMAT}${TEE_JSON_ARG:+ (tee -> ${TEE_JSON_ARG})}"
echo "[start-evse-hal] Python=${PY_BIN}"

# Validate critical dependencies early
if ! ip link show "${IFACE}" >/dev/null 2>&1; then
  echo "[start-evse-hal] ERROR: Interface '${IFACE}' not found. Set PLC_IFACE or use --iface." >&2
  exit 2
fi
if [[ -z "${ESP_PORT}" || ! -e "${ESP_PORT}" ]]; then
  echo "[start-evse-hal] ERROR: ESP CP UART not found. Set ESP_CP_PORT or use --port (e.g., /dev/serial0)." >&2
  exit 3
fi

# Best-effort: ensure PLC interface is up and allows multicast/promisc (needed for HPGP/SLAC)
if [[ "${EUID}" -ne 0 ]]; then
  sudo -n ip link set "${IFACE}" up || true
  sudo -n ip link set "${IFACE}" promisc on multicast on || true
else
  ip link set "${IFACE}" up || true
  ip link set "${IFACE}" promisc on multicast on || true
fi

run_cmd=( env PYTHONPATH="${PYTHONPATH_LOCAL}" "${CHILD_ENV[@]}" "${PY_BIN}" "${ARGS[@]}" )

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E "${run_cmd[@]}"
else
  exec "${run_cmd[@]}"
fi
