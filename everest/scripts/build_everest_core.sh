#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CORE_DIR="${ROOT}/everest-core"

if [[ ! -d "${CORE_DIR}" ]]; then
  echo "everest-core not found at ${CORE_DIR}. Clone submodule first." >&2
  exit 2
fi

sudo apt-get update && sudo apt-get install -y \
  cmake build-essential libssl-dev libboost-all-dev python3 python3-pip python3-venv

cd "${CORE_DIR}"
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
sudo cmake --install build

echo "everestd installed. You can run: everestd -c ${ROOT}/config/plc_only.yaml" >&2

