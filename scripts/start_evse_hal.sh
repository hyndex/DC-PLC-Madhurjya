#!/usr/bin/env bash
set -Eeuo pipefail

# Start EVSE in HAL mode with sensible defaults and live terminal logs.
# - Detects PLC interface and ESP CP serial port when not provided
# - Preserves env when escalating to root
# - Optional JSON tee to file when EVSE_TEE_JSON is set (path)

# --- Helpers ---
find_iface() {
  if [[ -n "${PLC_IFACE:-}" ]]; then echo "${PLC_IFACE}"; return; fi
  # Prefer an interface driven by qcaspi/qca7000
  for n in /sys/class/net/*; do
    i=$(basename "$n")
    if ethtool -i "$i" 2>/dev/null | grep -qi '^driver:\s*qcaspi\|^driver:\s*qca7000'; then
      echo "$i"; return;
    fi
  done
  # Common names
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

ensure_lladdr() {
  local nic="$1"
  # If already has a link-local addr, nothing to do
  if ip -6 addr show dev "$nic" | grep -q 'scope link'; then
    return 0
  fi
  # Compute EUI-64 from MAC and add fe80::/64 address
  local mac
  mac=$(cat "/sys/class/net/${nic}/address" 2>/dev/null | tr '[:lower:]' '[:upper:]') || return 0
  # MAC like FA:D3:C0:20:56:60
  IFS=: read -r o1 o2 o3 o4 o5 o6 <<<"$mac"
  # Flip the U/L bit
  printf -v o1_hex "%02X" $(( 0x${o1} ^ 0x02 ))
  local eui64="${o1_hex}:${o2}:${o3}:FF:FE:${o4}:${o5}:${o6}"
  # Condense to IPv6 form
  local ll="fe80::$(printf "%s" "$eui64" | awk -F: '{printf tolower($1$2":"$3$4":"$5$6":"$7$8)}')"
  # Enable IPv6 on nic if disabled
  if [ -f "/proc/sys/net/ipv6/conf/${nic}/disable_ipv6" ]; then
    sudo -n sh -c "echo 0 > /proc/sys/net/ipv6/conf/${nic}/disable_ipv6" 2>/dev/null || true
  fi
  sudo -n ip -6 addr add "${ll}/64" dev "$nic" scope link 2>/dev/null || true
}

# Heuristic: if PLC iface shows qcaspi and unhealthy stats, auto soft reset
maybe_auto_plc_reset() {
  local nic="$1"
  # Allow opt-out
  if [[ "${EVSE_PLC_SOFT_RESET_AUTO:-1}" == "0" ]]; then return; fi
  # Respect explicit request precedence
  if [[ "${EVSE_PLC_SOFT_RESET:-0}" != "0" ]]; then return; fi
  if ! ethtool -i "$nic" 2>/dev/null | grep -qi '^driver:\s*qcaspi'; then return; fi
  # Look for non-zero reset/bad signature counters
  if ethtool -S "$nic" 2>/dev/null | awk -F: '/(Triggered resets|Device resets|Bad signature)/{gsub(/ /,""); if($2+0>0) f=1} END{exit !f}'; then
    echo "[start-evse-hal] qcaspi stats suggest instability; auto soft-resetting ..."
    # Allow override of speed/burst via EVSE_PLC_AUTO_SOFT_RESET_*; default to 8 MHz / 5000
    QCASPI_CLKSPEED="${EVSE_PLC_AUTO_SOFT_RESET_SPEED:-8000000}" \
    QCASPI_BURST="${EVSE_PLC_AUTO_SOFT_RESET_BURST:-5000}" \
    QCASPI_PLUGGABLE="${QCASPI_PLUGGABLE:-1}" \
    sudo -n bash scripts/plc_soft_reset.sh || true
  fi
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
  EVSE_PLC_SOFT_RESET_AUTO  Auto soft-reset qcaspi on bad stats (default: 1)
  EVSE_PLC_SOFT_RESET       Force pre-run soft reset (default: 0)
  EVSE_PLC_AUTO_SOFT_RESET_SPEED  qcaspi clock (Hz) for reset (default: 8000000)
  EVSE_PLC_AUTO_SOFT_RESET_BURST  qcaspi burst len for reset (default: 5000)

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
# If a SECC .env is provided, try to read ISO EVSEID (does not affect SLAC ID)
if [[ -n "${SECC_CONFIG_PATH:-}" && -f "${SECC_CONFIG_PATH}" ]]; then
  id_from_env=$(grep -E '^EVSE_ID=' "${SECC_CONFIG_PATH}" | tail -n1 | cut -d '=' -f2- | tr -d ' \t\r') || true
else
  id_from_env=""
fi
IFACE="${IFACE_ARG:-$(find_iface)}"
ESP_PORT="${PORT_ARG:-$(find_port)}"
PY_BIN="$(find_python)"

export EVSE_CONTROLLER="${EVSE_CONTROLLER:-hal}"
export EVSE_HAL_ADAPTER="${ADAPTER_ARG}"
export EVSE_LOG_LEVEL="${EVSE_LOG_LEVEL:-INFO}"
export EVSE_LOG_FORMAT="${EVSE_LOG_FORMAT:-text}"
export EVSE_CP_HOST_HINTS="${EVSE_CP_HOST_HINTS:-0}"
export EVSE_CLEANUP_PREV="${EVSE_CLEANUP_PREV:-1}"
# Force unbuffered Python stdio and line-buffered stdio for child to reduce latency
export PYTHONUNBUFFERED="1"

# Optional cert/config envs
ARGS=( -m src.evse_main --evse-id "${EVSE_ID}" --iface "${IFACE}" --controller hal )
[[ -n "${SECC_CONFIG_PATH:-}" ]] && ARGS+=( --secc-config "${SECC_CONFIG_PATH}" )
if [[ -n "${SLAC_CONFIG_PATH:-}" ]]; then
  ARGS+=( --slac-config "${SLAC_CONFIG_PATH}" )
elif [[ -f "$PWD/slac.env" ]]; then
  SLAC_CONFIG_PATH="$PWD/slac.env"
  ARGS+=( --slac-config "${SLAC_CONFIG_PATH}" )
fi
[[ -n "${CERT_STORE_PATH:-}" ]] && ARGS+=( --cert-store "${CERT_STORE_PATH}" )

# Prepare env for child
CHILD_ENV=(
  # Use a single EVSE_ID consistently across SLAC + ISO. Also expose ISO_EVSE_ID alias.
  "EVSE_ID=${EVSE_ID}"
  ${id_from_env:+"ISO_EVSE_ID=${id_from_env}"}
  "EVSE_CONTROLLER=${EVSE_CONTROLLER}"
  "EVSE_HAL_ADAPTER=${EVSE_HAL_ADAPTER}"
  "EVSE_LOG_LEVEL=${EVSE_LOG_LEVEL}"
  "EVSE_LOG_FORMAT=${EVSE_LOG_FORMAT}"
  "EVSE_CP_HOST_HINTS=${EVSE_CP_HOST_HINTS}"
  # Let Python entrypoint auto-takeover the lock by terminating a prior holder
  "EVSE_LOCK_STEAL=1"
)
# Propagate any additional EVSE_* env vars not explicitly listed above
# so tuning flags like EVSE_SKIP_CABLE_CHECK and DC limits reach the child.
while IFS='=' read -r _k _v; do
  case "${_k}" in
    EVSE_*)
      case ",EVSE_ID,EVSE_CONTROLLER,EVSE_HAL_ADAPTER,EVSE_LOG_LEVEL,EVSE_LOG_FORMAT,EVSE_CP_HOST_HINTS," in
        *",${_k},"*) ;; # already present
        *) CHILD_ENV+=("${_k}=${!_k}");;
      esac
      ;;
  esac
done < <(env | grep -E '^EVSE_[A-Za-z0-9_]*=')
# If no DIN-specific ID provided, default DIN source to the same EVSE_ID for consistency
[[ -z "${EVSE_ID_DIN:-}" && -n "${EVSE_ID}" ]] && CHILD_ENV+=("EVSE_ID_DIN=${EVSE_ID}")
[[ -n "${ESP_PORT}" ]] && CHILD_ENV+=("ESP_CP_PORT=${ESP_PORT}")
[[ -n "${CERT_STORE_PATH:-}" ]] && CHILD_ENV+=("PKI_PATH=${CERT_STORE_PATH}")
[[ -n "${TEE_JSON_ARG}" ]] && CHILD_ENV+=("EVSE_LOG_JSON_TEE=${TEE_JSON_ARG}")

# Prefer local PySLAC without install
PYTHONPATH_LOCAL="${PYTHONPATH:-}:$PWD/src:$PWD/src/pyslac"

echo "[start-evse-hal] EVSE_ID=${EVSE_ID} IFACE=${IFACE} ADAPTER=${EVSE_HAL_ADAPTER} PORT=${ESP_PORT:-none}"
echo "[start-evse-hal] LOG_LEVEL=${EVSE_LOG_LEVEL} LOG_FORMAT=${EVSE_LOG_FORMAT}${TEE_JSON_ARG:+ (tee -> ${TEE_JSON_ARG})}"
echo "[start-evse-hal] Python=${PY_BIN}"

# Prepare tee file with permissive mode to avoid permission errors if root writes to it
if [[ -n "${TEE_JSON_ARG}" ]]; then
  sudo -n mkdir -p "$(dirname -- "${TEE_JSON_ARG}")" 2>/dev/null || true
  sudo -n rm -f "${TEE_JSON_ARG}" 2>/dev/null || true
  sudo -n touch "${TEE_JSON_ARG}" 2>/dev/null || true
  sudo -n chmod 666 "${TEE_JSON_ARG}" 2>/dev/null || true
fi

# Optional: cleanup any previous EVSE/SECC process to avoid port conflicts
if [[ "${EVSE_CLEANUP_PREV}" != "0" ]]; then
echo "[start-evse-hal] Cleaning up previous runs (if any) ..."
  # Kill Python invocations of src.evse_main (both -m and script path styles)
  pids=$(pgrep -f "python .* -m src.evse_main" || true)
  pids_alt=$(pgrep -f "python .*/src/evse_main.py" || true)
  pids_secc=$(pgrep -f "python .*/scripts/start_secc_only.py" || true)
  allpids="${pids} ${pids_alt} ${pids_secc}"
  if [[ -n "${allpids// }" ]]; then
    sudo -n kill -TERM ${allpids} 2>/dev/null || true
    # Wait briefly for clean shutdown
    for _ in 1 2 3 4 5; do
      sleep 0.2
      still=$(pgrep -f "python .* -m src.evse_main" || true)
      still_alt=$(pgrep -f "python .*/src/evse_main.py" || true)
      still_secc=$(pgrep -f "python .*/scripts/start_secc_only.py" || true)
      if [[ -z "${still}${still_alt}${still_secc}" ]]; then break; fi
    done
    # Force kill any stragglers
    sudo -n kill -KILL ${allpids} 2>/dev/null || true
  fi
  # Remove stale lock file if no EVSE Python process remains
  if [[ -z "$(pgrep -f 'python .* -m src.evse_main' || true)$(pgrep -f 'python .*/src/evse_main.py' || true)" ]]; then
    sudo -n rm -f /tmp/evse_main.lock 2>/dev/null || true
  fi
  # Free UDP 15118 listener
  if sudo -n ss -ulpn 2>/dev/null | grep -q "*:15118"; then
    pid=$(sudo -n ss -ulpn | awk '/\*:15118/{print $NF}' | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n1)
    if [[ -n "$pid" ]]; then sudo -n kill -KILL "$pid" 2>/dev/null || true; fi
  fi
fi

# Heuristic pre-run PLC soft reset if stats look unhealthy
maybe_auto_plc_reset "${IFACE}"

# Optional PLC soft reset (driver rebinding) for QCA7000/qcaspi before SLAC (default: enabled)
if [[ "${EVSE_PLC_SOFT_RESET:-1}" != "0" ]]; then
  if [[ -x "scripts/plc_soft_reset.sh" ]]; then
    echo "[start-evse-hal] Performing PLC soft reset (qcaspi) ..."
    sudo -n bash scripts/plc_soft_reset.sh || true
  fi
fi

# Best-effort: set CPU governor to performance for lower latency (no-fail)
if [[ "${EVSE_TUNE_CPU_GOVERNOR:-1}" != "0" ]]; then
  if command -v cpupower >/dev/null 2>&1; then
    sudo -n cpupower frequency-set -g performance >/dev/null 2>&1 || true
  else
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
      echo performance | sudo -n tee "$g" >/dev/null 2>&1 || true
    done
  fi
fi

# Best-effort: ensure PLC interface is up and allows multicast/promisc/allmulti (needed for HPGP/SLAC)
if [[ "${EUID}" -ne 0 ]]; then
  sudo -n ip link set "${IFACE}" up || true
  sudo -n ip link set "${IFACE}" promisc on multicast on allmulticast on || true
else
  ip link set "${IFACE}" up || true
  ip link set "${IFACE}" promisc on multicast on allmulticast on || true
fi
# Ensure IPv6 link‑local is configured on the chosen interface
ensure_lladdr "${IFACE}"

# Best-effort NIC tuning to improve SLAC reliability (safe no-ops when unsupported)
sudo -n ethtool -K "${IFACE}" gro off lro off rxvlan off txvlan off 2>/dev/null || true
sudo -n ethtool -G "${IFACE}" rx 512 tx 512 2>/dev/null || true
# Increase socket/driver buffers for raw packet capture
sudo -n sysctl -w net.core.rmem_max=33554432 net.core.rmem_default=262144 net.core.netdev_max_backlog=5000 >/dev/null 2>&1 || true

# Re-detect interface if the selected one disappeared after PLC reset
if ! ip link show "${IFACE}" >/dev/null 2>&1; then
  new_iface=$(find_iface)
  if [[ -n "${new_iface}" && "${new_iface}" != "${IFACE}" ]]; then
    echo "[start-evse-hal] Interface ${IFACE} not present; using ${new_iface}"
    IFACE="${new_iface}"
    # Update ARGS with new iface
    ARGS=( -m src.evse_main --evse-id "${EVSE_ID}" --iface "${IFACE}" --controller hal )
    [[ -n "${SECC_CONFIG_PATH:-}" ]] && ARGS+=( --secc-config "${SECC_CONFIG_PATH}" )
    [[ -n "${SLAC_CONFIG_PATH:-}" ]] && ARGS+=( --slac-config "${SLAC_CONFIG_PATH}" )
    [[ -n "${CERT_STORE_PATH:-}" ]] && ARGS+=( --cert-store "${CERT_STORE_PATH}" )
    # Bring up the new iface and ensure IPv6 link‑local
    if [[ "${EUID}" -ne 0 ]]; then
      sudo -n ip link set "${IFACE}" up || true
      sudo -n ip link set "${IFACE}" promisc on multicast on allmulticast on || true
    else
      ip link set "${IFACE}" up || true
      ip link set "${IFACE}" promisc on multicast on allmulticast on || true
    fi
    ensure_lladdr "${IFACE}"
  fi
fi

# Final validation after any iface changes / reset
if ! ip link show "${IFACE}" >/dev/null 2>&1; then
  echo "[start-evse-hal] ERROR: Interface '${IFACE}' not found. Set PLC_IFACE or use --iface." >&2
  exit 2
fi
if [[ -z "${ESP_PORT}" || ! -e "${ESP_PORT}" ]]; then
  echo "[start-evse-hal] ERROR: ESP CP UART not found. Set ESP_CP_PORT or use --port (e.g., /dev/serial0)." >&2
  exit 3
fi

# Optional EXI pre-warm to avoid first-encode latency (enabled by default)
if [[ "${EVSE_PREWARM_EXI:-1}" != "0" ]]; then
  echo "[start-evse-hal] Pre-warming EXI codec ..."
  env PYTHONPATH="${PYTHONPATH_LOCAL}" "${PY_BIN}" - <<'PY' >/dev/null 2>&1 || true
from iso15118.shared.exi_codec import EXI
from iso15118.shared.messages.app_protocol import SupportedAppProtocolRes, ResponseCodeSAP
from iso15118.shared.messages.enums import Namespace
try:
    exi = EXI()
    # Nudge codec by encoding a tiny SAP response
    _ = exi.to_exi(SupportedAppProtocolRes(response_code=ResponseCodeSAP.NEGOTIATION_OK, schema_id=0), Namespace.SAP)
except Exception:
    pass
PY
fi

# Ensure IPv6 link-local present on PLC iface for ISO 15118 TCP bind
ensure_lladdr "${IFACE}"

# Prefer line-buffered stdio to avoid I/O stalls when logs are piped
if command -v stdbuf >/dev/null 2>&1; then
  run_cmd=( env PYTHONPATH="${PYTHONPATH_LOCAL}" \
    "${CHILD_ENV[@]}" \
    LOG_LEVEL="${EVSE_LOG_LEVEL}" \
    stdbuf -oL -eL "${PY_BIN}" "${ARGS[@]}" )
else
  run_cmd=( env PYTHONPATH="${PYTHONPATH_LOCAL}" \
    "${CHILD_ENV[@]}" \
    LOG_LEVEL="${EVSE_LOG_LEVEL}" \
    "${PY_BIN}" "${ARGS[@]}" )
fi

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E "${run_cmd[@]}"
else
  exec "${run_cmd[@]}"
fi
