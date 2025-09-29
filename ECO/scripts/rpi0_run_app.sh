#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME=${1:-eco-rpi0}

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not in PATH." >&2
  exit 1
fi

OS_NAME=$(uname -s)

echo "Running ${IMAGE_NAME}:latest on host port 8000..."

# Choose interactive vs detached based on TTY availability
if [ -t 1 ]; then
  RUN_FLAGS="-it"
else
  RUN_FLAGS="-d"
fi

if [[ "$OS_NAME" == "Linux" ]]; then
  # host network is supported on Linux
  docker run --rm ${RUN_FLAGS} \
    --network host \
    -e PKI_PATH=${PKI_PATH:-} \
    "${IMAGE_NAME}:latest"
else
  # Mac/Windows: fall back to port publishing
  docker run --rm ${RUN_FLAGS} \
    -e PKI_PATH=${PKI_PATH:-} \
    -p 8000:8000 \
    "${IMAGE_NAME}:latest"
fi
