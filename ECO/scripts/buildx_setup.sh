#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not in PATH." >&2
  exit 1
fi

# Install binfmt for QEMU emulation (required for arm/v6 cross-build)
docker run --privileged --rm tonistiigi/binfmt --install all >/dev/null

# Create and select a buildx builder if it doesn't exist
BUILDER_NAME="rpi0-builder"
if docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
  docker buildx use "${BUILDER_NAME}" >/dev/null
else
  docker buildx create --name "${BUILDER_NAME}" --use >/dev/null
fi

# Initialize the builder
docker buildx inspect --bootstrap >/dev/null
echo "buildx ready (builder: ${BUILDER_NAME})"
