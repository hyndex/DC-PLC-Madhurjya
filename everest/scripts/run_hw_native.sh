#!/usr/bin/env bash
set -euo pipefail

# Native run (outside Docker) for hardware mode
# Requirements: everest-core installed so that 'manager' is available in PATH or at /usr/local/bin/manager

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# Prefer system config if present, else repo config
CONFIG="${CONFIG:-}"
if [[ -z "${CONFIG}" ]]; then
  if [[ -f "/etc/everest/plc_only.yaml" ]]; then
    CONFIG="/etc/everest/plc_only.yaml"
  else
    CONFIG="${ROOT}/config/plc_only.yaml"
  fi
fi
# Prefer system env if present, else repo .env
ENV_FILE="${ENV_FILE:-}"
if [[ -z "${ENV_FILE}" ]]; then
  if [[ -f "/etc/everest/ev.env" ]]; then
    ENV_FILE="/etc/everest/ev.env"
  else
    ENV_FILE="${ROOT}/.env"
  fi
fi

if [[ -f "${ENV_FILE}" ]]; then
  set -a; source "${ENV_FILE}"; set +a
fi

# Pre-flight: ensure PLC iface is up and IPv6 LL exists
IFACE="${PLC_IFACE:-eth1}"
if command -v ip >/dev/null 2>&1; then
  sudo ip link set dev "$IFACE" up 2>/dev/null || true
  sudo sysctl -w net.ipv6.conf.all.disable_ipv6=0 >/dev/null 2>&1 || true
  sudo sysctl -w "net.ipv6.conf.${IFACE}.disable_ipv6=0" >/dev/null 2>&1 || true
  if ! ip -6 addr show dev "$IFACE" | grep -q 'fe80::'; then
    sudo ip -6 addr add fe80::2/64 dev "$IFACE" scope link 2>/dev/null || true
  fi
fi

# Optional: ensure required caps are set on SLAC/V2G binaries
if command -v setcap >/dev/null 2>&1; then
  for bin in \
    "/usr/local/libexec/everest/modules/EvseSlac/EvseSlac" \
    "/usr/local/libexec/everest/modules/EvseV2G/EvseV2G"; do
    if [[ -x "$bin" ]]; then
      sudo setcap cap_net_raw,cap_net_admin=eip "$bin" 2>/dev/null || true
    fi
  done
fi

BIN="${BIN:-$(command -v manager || true)}"
if [[ -z "${BIN}" ]]; then
  if [[ -x "/opt/everest/everest-core/build/dist/bin/manager" ]]; then
    BIN="/opt/everest/everest-core/build/dist/bin/manager"
  elif [[ -x "/usr/local/bin/manager" ]]; then
    BIN="/usr/local/bin/manager"
  else
    echo "manager binary not found; please install everest-core" >&2
    exit 127
  fi
fi

exec "${BIN}" --config "${CONFIG}"
