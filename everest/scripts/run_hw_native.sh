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
