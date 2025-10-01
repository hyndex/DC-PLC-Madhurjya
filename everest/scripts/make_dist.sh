#!/usr/bin/env bash
set -euo pipefail

# Build and package an EVerest "dist" tarball that can be consumed by
# setup_native_pi.sh via USE_DIST=/path/to/dist.tar.gz
#
# The tarball layout is the contents of everest-core/build/dist, e.g.:
#   bin/manager
#   libexec/everest/...
#   etc/everest/...
#   share/everest/...
#
# Usage:
#   everest/scripts/make_dist.sh [-o OUTPUT] [--no-build] [--jobs N] [--cmake-opts "..."]
#
# Examples:
#   everest/scripts/make_dist.sh
#   everest/scripts/make_dist.sh -o /tmp/everest-dist-$(uname -m).tar.gz
#   JOBS=4 CMAKE_OPTS="-DCMAKE_BUILD_TYPE=Release" everest/scripts/make_dist.sh

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CORE_DIR="${ROOT}/everest-core"
BUILD_DIR="${CORE_DIR}/build"
DIST_DIR="${BUILD_DIR}/dist"
JOBS="${JOBS:-$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
CMAKE_OPTS_DEFAULT="-DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DCMAKE_RUN_CLANG_TIDY=OFF -DEVEREST_ENABLE_RUN_SCRIPT_GENERATION=OFF -DISO15118_2_GENERATE_AND_INSTALL_CERTIFICATES=OFF"
CMAKE_OPTS="${CMAKE_OPTS:-${CMAKE_OPTS_DEFAULT}}"
OUTPUT=""
DO_BUILD=1

usage() {
  cat <<EOF
Usage: $0 [-o OUTPUT] [--no-build] [--jobs N] [--cmake-opts "..."]

Options:
  -o PATH         Output tar.gz path (default: /tmp/everest-dist-ARCH-YYYYmmddHHMMSS.tar.gz)
  --no-build      Do not run CMake build/install; just package existing dist
  --jobs N        Parallel build jobs (default: ${JOBS})
  --cmake-opts S  Extra CMake options (default minimal flags)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o)
      OUTPUT="$2"; shift 2;;
    --no-build)
      DO_BUILD=0; shift;;
    --jobs)
      JOBS="$2"; shift 2;;
    --cmake-opts)
      CMAKE_OPTS="$2"; shift 2;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ ! -d "${CORE_DIR}" ]]; then
  echo "everest-core not found at ${CORE_DIR}" >&2
  exit 2
fi

if [[ "${DO_BUILD}" -eq 1 ]]; then
  echo "[make-dist] Configuring/building with minimal flags: ${CMAKE_OPTS} (jobs=${JOBS})" >&2
  (
    cd "${CORE_DIR}"
    git submodule update --init --recursive || true
    cmake -B build -S . ${CMAKE_OPTS}
    cmake --build build -j"${JOBS}"
    cmake --install build
  )
else
  echo "[make-dist] Skipping build (--no-build). Packaging existing dist." >&2
fi

if [[ ! -x "${DIST_DIR}/bin/manager" ]]; then
  echo "[make-dist] manager not found in ${DIST_DIR}/bin. Did the build/install succeed?" >&2
  exit 3
fi

arch=$(uname -m)
ts=$(date +%Y%m%d%H%M%S)
OUTPUT_DEFAULT="/tmp/everest-dist-${arch}-${ts}.tar.gz"
OUTPUT="${OUTPUT:-${OUTPUT_DEFAULT}}"

echo "[make-dist] Creating tarball: ${OUTPUT}" >&2
mkdir -p "$(dirname "${OUTPUT}")"
tar -C "${DIST_DIR}" -czf "${OUTPUT}" .
echo "[make-dist] Done." >&2
echo "${OUTPUT}"

