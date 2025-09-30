#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)

ENV_FILE="${ROOT}/.env"
CONFIG="${ROOT}/config/plc_only.yaml"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

echo "[everest] Starting PLC-only stack with config: ${CONFIG}" >&2
echo "[everest] Using 'manager' runtime from everest-core." >&2

MANAGER_BIN="${MANAGER_BIN:-/opt/everest/everest-core/build/dist/bin/manager}"
if command -v manager >/dev/null 2>&1; then
  exec manager --config "${CONFIG}"
elif [[ -x "${MANAGER_BIN}" ]]; then
  exec "${MANAGER_BIN}" --config "${CONFIG}"
else
  echo "manager binary not found. Build everest-core and install the runtime." >&2
  echo "Hint: cmake -B everest/everest-core/build -S everest/everest-core && cmake --build everest/everest-core/build && sudo cmake --install everest/everest-core/build" >&2
  exit 127
fi
