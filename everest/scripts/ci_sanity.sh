#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

echo "[ci] Building image (no cache)"
docker build --no-cache -t everest-plc -f "$ROOT/docker/Dockerfile" "$ROOT"

echo "[ci] Checking plc_only_sim.yaml"
docker run --rm -v "$ROOT/config":/opt/everest/config everest-plc \
  /opt/everest/everest-core/build/dist/bin/manager --check --config /opt/everest/config/plc_only_sim.yaml

echo "[ci] Checking plc_only.yaml"
docker run --rm -v "$ROOT/config":/opt/everest/config everest-plc \
  /opt/everest/everest-core/build/dist/bin/manager --check --config /opt/everest/config/plc_only.yaml

echo "[ci] Checking plc_only_pnc.yaml"
docker run --rm -v "$ROOT/config":/opt/everest/config everest-plc \
  /opt/everest/everest-core/build/dist/bin/manager --check --config /opt/everest/config/plc_only_pnc.yaml

echo "[ci] OK"

