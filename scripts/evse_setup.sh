#!/usr/bin/env bash
# Comprehensive EVSE setup and hardening script
# - Disables unneeded services (keeps Wi‑Fi)
# - Picks a primary interface with eth0→eth1→en*→wlan0 fallback
# - Manages a project .env (init, set, export, re-export)
# - Optionally installs deps and checks ports
#
# Usage examples:
#   sudo scripts/evse_setup.sh all                # do everything (safe defaults)
#   scripts/evse_setup.sh env init                # create .env with smart defaults
#   scripts/evse_setup.sh env set KEY=VALUE       # update .env (idempotent)
#   source scripts/evse_setup.sh env export       # export to current shell
#   sudo scripts/evse_setup.sh services           # disable Bluetooth, Avahi, etc (keeps Wi‑Fi)
#   scripts/evse_setup.sh iface print             # print the selected primary interface
#
# Idempotent; safe to re-run.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)

ENV_FILE_DEFAULT="${REPO_ROOT}/.env"
EXPORT_FILE_DEFAULT="${REPO_ROOT}/scripts/export_env.sh"

# ------------- helpers -------------
log() { printf "[setup] %s\n" "$*"; }
warn() { printf "[setup][WARN] %s\n" "$*" 1>&2; }
err() { printf "[setup][ERROR] %s\n" "$*" 1>&2; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

require_root() {
  if [ "$(id -u)" != "0" ]; then
    err "This action requires root. Re-run with sudo."
    exit 1
  fi
}

is_linux() { [ "$(uname -s)" = "Linux" ]; }
is_macos() { [ "$(uname -s)" = "Darwin" ]; }

is_systemd() {
  is_linux || return 1
  have_cmd systemctl || return 1
  # Basic sanity: PID 1 is systemd
  [ "$(basename "$(readlink -f /sbin/init 2>/dev/null || echo /sbin/init)")" = "systemd" ] || return 0
}

# ------------- .env management -------------
ENV_FILE="${ENV_FILE:-${ENV_FILE_DEFAULT}}"
EXPORT_FILE="${EXPORT_FILE:-${EXPORT_FILE_DEFAULT}}"

choose_primary_iface() {
  # Prefer an UP interface: eth0 -> eth[1..] -> en* -> wlan0 -> wl*
  # Skip loopback and qcaspi/plc
  local picked=""
  local cand_list=""

  if have_cmd ip; then
    cand_list=$(ip -o link show | awk -F ': ' '{print $2}' | grep -Ev '^(lo)$' || true)
  else
    cand_list=$(ls -1 /sys/class/net 2>/dev/null | grep -Ev '^(lo)$' || true)
  fi

  # Build ordered list as newline-separated string
  {
    echo "eth0"
    echo "$cand_list" | awk '/^eth[1-9][0-9]*$/'
    echo "$cand_list" | awk '/^en/'
    echo "wlan0"
    echo "$cand_list" | awk '/^wl/'
  } | awk '!seen[$0]++' | while IFS= read -r i; do
    [ -n "$i" ] || continue
    # Check existence
    [ -e "/sys/class/net/${i}" ] || continue
    # Exclude qcaspi PLC
    if have_cmd ethtool && ethtool -i "$i" 2>/dev/null | grep -qi '^driver: *qcaspi'; then
      continue
    fi
    # If ip is present, prefer UP state first
    if have_cmd ip && ip -o link show "$i" 2>/dev/null | grep -q 'state UP'; then
      picked="$i"; echo "$picked"; return 0
    fi
    # Otherwise remember first valid candidate
    if [ -z "$picked" ]; then picked="$i"; fi
  done

  if [ -n "$picked" ]; then
    printf "%s" "$picked"
  else
    printf "%s" "lo"
  fi
}

env_init() {
  local env_file="${1:-$ENV_FILE}"
  if [ -f "$env_file" ]; then
    log ".env already exists: $env_file"
    return 0
  fi
  local iface
  iface=$(choose_primary_iface || true)
  cat >"$env_file" <<EOF
# EVSE project environment
# Generated: $(date -Iseconds)

# EVSE identity and defaults
EVSE_ID=${EVSE_ID:-DEMO_EVSE}
EVSE_CONTROLLER=${EVSE_CONTROLLER:-sim}

# Networking
PRIMARY_IFACE=${PRIMARY_IFACE:-$iface}
ISO15118_PORT=${ISO15118_PORT:-15118}
API_PORT=${API_PORT:-8000}

# Virtualenv location
VENV_DIR=${VENV_DIR:-${REPO_ROOT}/.venv}

# Logging level
LOG_LEVEL=${LOG_LEVEL:-INFO}

# HAL/Serial defaults (update as needed)
EVSE_HAL_ADAPTER=${EVSE_HAL_ADAPTER:-sim}
ESP_SERIAL_PORT=${ESP_SERIAL_PORT:-/dev/ttyUSB0}
ESP_BAUD=${ESP_BAUD:-115200}

EOF
  log "Created $env_file"
}

env_print() {
  local env_file="${1:-$ENV_FILE}"
  [ -f "$env_file" ] || { err "Missing $env_file"; exit 1; }
  cat "$env_file"
}

env_set() {
  local env_file="${1:-$ENV_FILE}"
  local kv="${2:-}"
  [ -n "$kv" ] || { err "Usage: env set KEY=VALUE"; exit 1; }
  case "$kv" in
    *=*) : ;;
    *) err "Invalid format. Use KEY=VALUE"; exit 1 ;;
  esac
  local key="${kv%%=*}" val="${kv#*=}"
  touch "$env_file"
  if grep -qE "^${key}=" "$env_file"; then
    # Replace existing
    if is_macos; then
      sed -i '' -E "s|^${key}=.*$|${key}=${val}|" "$env_file"
    else
      sed -i -E "s|^${key}=.*$|${key}=${val}|" "$env_file"
    fi
  else
    printf "%s=%s\n" "$key" "$val" >>"$env_file"
  fi
  log "Updated $key in $env_file"
}

env_export() {
  local env_file="${1:-$ENV_FILE}"
  local out_file="${2:-$EXPORT_FILE}"
  [ -f "$env_file" ] || { err "Missing $env_file. Run: env init"; exit 1; }
  # shellcheck disable=SC2013
  {
    echo "# Auto-generated from $env_file"
    echo "# shellcheck shell=bash"
    while IFS= read -r line; do
      [[ -z "$line" || "$line" =~ ^# ]] && continue
      # preserve content as-is; safest is to export verbatim
      key="${line%%=*}"; val="${line#*=}"
      printf 'export %s=%q\n' "$key" "$val"
    done <"$env_file"
  } >"$out_file"
  chmod +x "$out_file" || true
  log "Generated export script: $out_file"
  log "To export into current shell: source $out_file"
}

env_apply_global() {
  require_root
  local env_file="${1:-$ENV_FILE}"
  [ -f "$env_file" ] || { err "Missing $env_file. Run: env init"; exit 1; }
  local target="/etc/profile.d/evse_env.sh"
  env_export "$env_file" "/tmp/evse_env.sh"
  install -m 0644 "/tmp/evse_env.sh" "$target"
  rm -f "/tmp/evse_env.sh"
  log "Installed global env to $target (effective for new shells)"
}

# ------------- service hardening -------------
disable_service_safe() {
  local unit="$1"
  is_systemd || { warn "systemd not detected; skipping $unit"; return 0; }
  systemctl is-enabled "$unit" >/dev/null 2>&1 || true
  if systemctl list-unit-files | awk '{print $1}' | grep -qx "$unit"; then
    log "Disabling $unit"
    systemctl disable "$unit" >/dev/null 2>&1 || true
    systemctl stop "$unit" >/dev/null 2>&1 || true
    # Mask only for services known to auto-spawn
    case "$unit" in
      bluetooth.service|hciuart.service|ModemManager.service|avahi-daemon.service)
        systemctl mask "$unit" >/dev/null 2>&1 || true ;;
    esac
  else
    log "Service not present: $unit (ok)"
  fi
}

services_harden() {
  require_root
  # Defaults: keep web servers; disable serial-getty to free UART; disable bt/avahi/modem/cups
  local DISABLE_WEB="${DISABLE_WEB:-0}"
  local DISABLE_SERIAL_GETTY="${DISABLE_SERIAL_GETTY:-1}"
  local DISABLE_BLUETOOTH="${DISABLE_BLUETOOTH:-1}"
  local DISABLE_AVAHI="${DISABLE_AVAHI:-1}"
  local DISABLE_MODEM="${DISABLE_MODEM:-1}"
  local DISABLE_CUPS="${DISABLE_CUPS:-1}"

  log "Hardening services (keeps Wi‑Fi/network core; web kept=${DISABLE_WEB})"

  local to_disable=()
  if [ "$DISABLE_BLUETOOTH" = "1" ]; then
    to_disable+=(bluetooth.service hciuart.service)
  fi
  if [ "$DISABLE_AVAHI" = "1" ]; then
    to_disable+=(avahi-daemon.service)
  fi
  if [ "$DISABLE_MODEM" = "1" ]; then
    to_disable+=(ModemManager.service)
  fi
  if [ "$DISABLE_CUPS" = "1" ]; then
    to_disable+=(cups.service cups-browsed.service)
  fi
  if [ "$DISABLE_SERIAL_GETTY" = "1" ]; then
    # Serial consoles that can steal UART used by ESP/CP
    to_disable+=(serial-getty@ttyAMA0.service serial-getty@ttyS0.service serial-getty@ttyUSB0.service)
  else
    warn "Serial getty kept enabled; UART devices may be locked by login console."
  fi
  if [ "$DISABLE_WEB" = "1" ]; then
    to_disable+=(nginx.service apache2.service lighttpd.service)
  fi

  for s in "${to_disable[@]}"; do
    disable_service_safe "$s"
  done

  # If web servers kept, check for port conflicts and warn
  if [ "$DISABLE_WEB" = "0" ]; then
    local api_port iso_port
    api_port=$(grep -E '^API_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo 8000)
    iso_port=$(grep -E '^ISO15118_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo 15118)
    if have_cmd lsof; then
      if lsof -iTCP -sTCP:LISTEN -P -n | awk -v a=":${api_port}$" -v b=":${iso_port}$" 'tolower($1)~/^(nginx|apache2|httpd|lighttpd)$/ && ($9~a || $9~b) {print; found=1} END{exit !found}'; then
        warn "A web server is listening on API/ISO port (${api_port}/${iso_port}). Consider reconfiguring or run with DISABLE_WEB=1."
      fi
    elif have_cmd ss; then
      if ss -ltnp | awk -v a=":${api_port}$" -v b=":${iso_port}$" 'tolower($0)~/nginx|apache2|httpd|lighttpd/ && ($4~a || $4~b) {print; found=1} END{exit !found}'; then
        warn "A web server is listening on API/ISO port (${api_port}/${iso_port}). Consider reconfiguring or run with DISABLE_WEB=1."
      fi
    fi
  fi

  # Show summary of listeners after changes
  if have_cmd ss; then
    log "Active TCP listeners after hardening:"; ss -ltnp || true
  elif have_cmd netstat; then
    netstat -ltnp || true
  fi
}

# ------------- deps and venv -------------
install_deps() {
  require_root
  if is_linux && have_cmd apt-get; then
    export DEBIAN_FRONTEND=noninteractive
    log "Installing system deps via apt-get"
    apt-get update -y
    apt-get install -y \
      python3 python3-venv python3-pip python3-dev \
      build-essential libssl-dev libffi-dev \
      libpcap0.8 libpcap0.8-dev \
      git iproute2 iputils-ping net-tools curl ethtool
  else
    warn "apt-get not available; skipping system deps"
  fi
}

ensure_venv_and_pip() {
  local venv_dir
  venv_dir=$(grep -E '^VENV_DIR=' "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2- || true)
  venv_dir=${venv_dir:-${REPO_ROOT}/.venv}
  if [ ! -d "$venv_dir" ]; then
    log "Creating Python virtualenv at $venv_dir"
    python3 -m venv "$venv_dir"
  fi
  # Activate
  # shellcheck disable=SC1090
  source "$venv_dir/bin/activate"
  python -V
  pip -V
  log "Upgrading pip/setuptools/wheel"
  pip install --upgrade pip setuptools wheel
  if [ -f "${REPO_ROOT}/requirements.txt" ]; then
    log "Installing Python requirements"
    pip install -r "${REPO_ROOT}/requirements.txt"
  fi
  # Install local submodules (if present) to keep versions aligned
  if [ -d "${REPO_ROOT}/src/iso15118/iso15118" ]; then
    pip install -e "${REPO_ROOT}/src/iso15118" --no-deps || true
  fi
  if [ -d "${REPO_ROOT}/src/pyslac/pyslac" ]; then
    pip install -e "${REPO_ROOT}/src/pyslac" --no-deps || true
  fi
  if [ -f "${REPO_ROOT}/requirements-submodules.txt" ]; then
    pip install -r "${REPO_ROOT}/requirements-submodules.txt" || true
  fi
}

# ------------- ports check -------------
ports_check() {
  local env_file="${1:-$ENV_FILE}"
  local api_port iso_port
  api_port=$(grep -E '^API_PORT=' "$env_file" 2>/dev/null | cut -d= -f2- || echo 8000)
  iso_port=$(grep -E '^ISO15118_PORT=' "$env_file" 2>/dev/null | cut -d= -f2- || echo 15118)
  log "Checking listeners on API_PORT=$api_port and ISO15118_PORT=$iso_port"
  if have_cmd lsof; then
    lsof -iTCP -sTCP:LISTEN -P -n | awk -v a="${api_port}" -v b="${iso_port}" 'NR==1 || $9 ~ ":"a"$" || $9 ~ ":"b"$"'
  elif have_cmd ss; then
    ss -ltnp | awk -v a=":${api_port}$" -v b=":${iso_port}$" 'NR==1 || $4 ~ a || $4 ~ b'
  else
    warn "Neither lsof nor ss found to check ports"
  fi
}

# ------------- iface helpers -------------
iface_print() {
  local i
  i=$(choose_primary_iface)
  log "Primary interface: $i"
}

iface_update_env() {
  local i
  i=$(choose_primary_iface)
  env_set "$ENV_FILE" "PRIMARY_IFACE=${i}"
}

# ------------- dispatcher -------------
usage() {
  cat <<USAGE
Usage: $0 <command> [args]

Commands:
  all                      Run env init, export, deps (no apt on mac), harden services
  services [opts]          Disable Bluetooth/Avahi/ModemManager/CUPS; serial-getty by default. Keeps Wi‑Fi.
                           Options (env or flags):
                             --disable-web            Also disable nginx/apache/lighttpd (default: keep)
                             --keep-serial            Keep serial-getty (not recommended for ESP UART)
                           Env toggles (default in brackets):
                             DISABLE_WEB=[0] DISABLE_SERIAL_GETTY=[1]
                             DISABLE_BLUETOOTH=[1] DISABLE_AVAHI=[1]
                             DISABLE_MODEM=[1] DISABLE_CUPS=[1]
  deps                     Install system deps (apt) if available
  venv                     Create/upgrade venv and install project deps
  ports                    Check listeners on configured ports
  iface print              Print selected primary interface
  iface update-env         Detect iface and update PRIMARY_IFACE in .env

  env init                 Create .env with sane defaults
  env print                Print .env
  env set KEY=VALUE        Update or add a key in .env
  env export               Generate scripts/export_env.sh (to use: source scripts/export_env.sh)
  env apply-global         Install env to /etc/profile.d/evse_env.sh (root)

Environment:
  ENV_FILE   Path to .env (default: ${ENV_FILE_DEFAULT})
  EXPORT_FILE  Path to export script (default: ${EXPORT_FILE_DEFAULT})

Examples:
  sudo $0 all
  $0 env init && $0 env export && source ${EXPORT_FILE_DEFAULT}
  sudo $0 services
  $0 iface update-env
USAGE
}

cmd=${1:-}
case "$cmd" in
  all)
    shift || true
    env_init "$ENV_FILE"
    iface_update_env
    env_export "$ENV_FILE" "$EXPORT_FILE"
    if is_linux; then install_deps || true; fi
    ensure_venv_and_pip || true
    services_harden || true
    ports_check "$ENV_FILE" || true
    log "All done. Consider: source $EXPORT_FILE"
    ;;
  services)
    # Parse optional flags for this subcommand
    shift || true
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --disable-web) DISABLE_WEB=1 ;;
        --keep-serial) DISABLE_SERIAL_GETTY=0 ;;
        --keep-bluetooth) DISABLE_BLUETOOTH=0 ;;
        --keep-avahi) DISABLE_AVAHI=0 ;;
        --keep-modem) DISABLE_MODEM=0 ;;
        --keep-cups) DISABLE_CUPS=0 ;;
        *) warn "Unknown flag for services: $1" ;;
      esac
      shift || true
    done
    services_harden ;;
  deps)
    install_deps ;;
  venv)
    ensure_venv_and_pip ;;
  ports)
    ports_check "$ENV_FILE" ;;
  iface)
    sub=${2:-}
    case "$sub" in
      print) iface_print ;;
      update-env) iface_update_env ;;
      *) usage; exit 1 ;;
    esac
    ;;
  env)
    sub=${2:-}
    case "$sub" in
      init) env_init "$ENV_FILE" ;;
      print) env_print "$ENV_FILE" ;;
      set)
        env_set "$ENV_FILE" "${3:-}" ;;
      export) env_export "$ENV_FILE" "$EXPORT_FILE" ;;
      apply-global) env_apply_global "$ENV_FILE" ;;
      *) usage; exit 1 ;;
    esac
    ;;
  -h|--help|help|"")
    usage ;;
  *)
    usage; exit 1 ;;
esac
