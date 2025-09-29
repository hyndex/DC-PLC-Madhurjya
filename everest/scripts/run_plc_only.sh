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
echo "[everest] Note: ensure everest-core is built and 'everestd' is available in PATH." >&2

# If everestd is installed, run it; otherwise, log instruction
if command -v everestd >/dev/null 2>&1; then
  exec everestd -c "${CONFIG}"
else
  echo "everestd not found. Build everest-core and install the runtime." >&2
  echo "Hint: in everest-core: cmake -B build -S . && cmake --build build && sudo cmake --install build" >&2
  exit 127
fi

