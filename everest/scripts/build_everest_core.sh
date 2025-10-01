#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CORE_DIR="${ROOT}/everest-core"
JOBS="${JOBS:-$(nproc || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
# Minimal cmake options to speed up builds on low-power devices
# Build only the modules needed for PLC-only DC profile by default
NEEDED_MODULES="EvseSlac;EvseV2G;EvseManager;EvseSecurity"
CMAKE_OPTS_DEFAULT="-DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DCMAKE_RUN_CLANG_TIDY=OFF -DEVEREST_ENABLE_RUN_SCRIPT_GENERATION=OFF -DISO15118_2_GENERATE_AND_INSTALL_CERTIFICATES=OFF -DEVEREST_INCLUDE_MODULES=${NEEDED_MODULES}"
CMAKE_OPTS="${CMAKE_OPTS:-${CMAKE_OPTS_DEFAULT}}"

if [[ ! -d "${CORE_DIR}" ]]; then
  echo "everest-core not found at ${CORE_DIR}. Clone submodule first." >&2
  exit 2
fi

sudo apt-get update && sudo apt-get install -y \
  cmake build-essential libssl-dev libboost-all-dev libpcap-dev libevent-dev libcap-dev libsqlite3-dev \
  python3 python3-pip python3-venv ethtool

cd "${CORE_DIR}"
cmake -B build -S . ${CMAKE_OPTS}
cmake --build build -j"${JOBS}"
sudo cmake --install build

echo "everestd installed. You can run: everestd -c ${ROOT}/config/plc_only.yaml" >&2
