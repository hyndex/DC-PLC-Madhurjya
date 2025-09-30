#!/usr/bin/env bash
set -euo pipefail

# Native run (outside Docker) for hardware mode
# Requirements: everest-core installed so that 'manager' is available in PATH or at /usr/local/bin/manager

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CONFIG="${CONFIG:-${ROOT}/config/plc_only.yaml}"
ENV_FILE="${ENV_FILE:-${ROOT}/.env}"

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

