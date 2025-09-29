#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME=${1:-eco-rpi0}

"$(dirname "$0")"/buildx_setup.sh

echo "Building and running tests for linux/arm/v6..."
docker buildx build \
  --platform linux/arm/v6 \
  --target test \
  --progress=plain \
  -t "${IMAGE_NAME}:test" \
  --load \
  -f docker/Dockerfile.rpi0 \
  .

echo "Tests finished successfully for arm/v6."

