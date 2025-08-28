#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME=${1:-eco-rpi0}

"$(dirname "$0")"/buildx_setup.sh

echo "Building runtime image for linux/arm/v6..."
docker buildx build \
  --platform linux/arm/v6 \
  --progress=plain \
  -t "${IMAGE_NAME}:latest" \
  --load \
  -f docker/Dockerfile.rpi0 \
  .

echo "Built image: ${IMAGE_NAME}:latest (arm/v6)"

