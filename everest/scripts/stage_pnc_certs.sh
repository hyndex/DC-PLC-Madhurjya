#!/usr/bin/env bash
set -euo pipefail

# Copies auto-generated demo certificates from everest-core install dist into a writable certs dir.

DIST_ROOT="${DIST_ROOT:-/opt/everest/everest-core/build/dist}"
DEST="${DEST:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/certs}"

echo "[pnc] staging demo certs from ${DIST_ROOT}/etc/everest/certs to ${DEST}"
mkdir -p "${DEST}"
rsync -av --delete "${DIST_ROOT}/etc/everest/certs/" "${DEST}/"
echo "[pnc] done"

