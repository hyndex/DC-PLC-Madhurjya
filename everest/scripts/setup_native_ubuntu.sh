#!/usr/bin/env bash
# Native end-to-end setup for Ubuntu (x86_64 or arm64)
# - Installs build/runtime deps
# - Builds and installs everest-core (manager) with minimal flags
# - Installs Joulepoint modules (HAL + derate)
# - Installs configs and optional PnC demo certs
# - Installs and enables systemd services (optional on dev hosts)

set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
DIST_ROOT_DEFAULT="${ROOT}/everest-core/build/dist"
PREFIX="/usr/local"
ETC_DIR="/etc/everest"
SYSTEMD_DIR="/etc/systemd/system"
JOBS="${JOBS:-$(nproc || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
# Minimal cmake flags to speed up build on dev machines
CMAKE_OPTS_DEFAULT="-DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DCMAKE_RUN_CLANG_TIDY=OFF -DEVEREST_ENABLE_RUN_SCRIPT_GENERATION=OFF -DISO15118_2_GENERATE_AND_INSTALL_CERTIFICATES=OFF"
CMAKE_OPTS="${CMAKE_OPTS:-${CMAKE_OPTS_DEFAULT}}"

log() { printf "[setup-ubuntu] %s\n" "$*"; }
err() { printf "[setup-ubuntu][ERROR] %s\n" "$*" 1>&2; }

require_root() { if [[ $(id -u) -ne 0 ]]; then err "Run as root (sudo)."; exit 1; fi; }

step_pkgs() {
  log "Installing system dependencies"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git rsync curl ca-certificates pkg-config ethtool \
    build-essential cmake \
    python3 python3-pip python3-venv python3-serial \
    libssl-dev libboost-all-dev libsqlite3-dev \
    libpcap-dev libevent-dev libcap-dev \
    clang-tidy cppcheck
}

step_build_everest_core() {
  log "Building everest-core (manager) [minimal flags]"
  cd "${ROOT}/everest-core"
  git submodule update --init --recursive || true
  cmake -B build -S . ${CMAKE_OPTS}
  cmake --build build -j"${JOBS}"
  cmake --install build
  command -v manager >/dev/null 2>&1 || err "manager not in PATH after install"
}

step_install_modules() {
  log "Installing Joulepoint modules (HAL + derate)"
  install -d "${PREFIX}/libexec/everest/modules/esp32_hal_adapter" \
             "${PREFIX}/libexec/everest/modules/evse_params_provider"
  rsync -a "${ROOT}/modules/esp32_hal_adapter/" "${PREFIX}/libexec/everest/modules/esp32_hal_adapter/"
  rsync -a "${ROOT}/modules/evse_params_provider/" "${PREFIX}/libexec/everest/modules/evse_params_provider/"
  install -d "${PREFIX}/libexec/everest/scripts"
  install -m 0755 "${ROOT}/scripts/qca_watchdog.sh" "${PREFIX}/libexec/everest/scripts/qca_watchdog.sh"
  install -m 0755 "${ROOT}/scripts/qca_health.sh" "${PREFIX}/libexec/everest/scripts/qca_health.sh"
  install -m 0755 "${ROOT}/scripts/plc_soft_reset.sh" "${PREFIX}/libexec/everest/scripts/plc_soft_reset.sh"
}

step_install_configs() {
  log "Installing configs under ${ETC_DIR}"
  install -d "${ETC_DIR}"
  install -m 0644 "${ROOT}/config/plc_only.yaml" "${ETC_DIR}/plc_only.yaml"
  install -m 0644 "${ROOT}/config/plc_only_pnc.yaml" "${ETC_DIR}/plc_only_pnc.yaml"
  if [[ ! -f "${ETC_DIR}/ev.env" ]]; then
    cat > "${ETC_DIR}/ev.env" <<EOF
# EVerest PLC-only env (Ubuntu)
PLC_IFACE=eth0
SLAC_NMK=00112233445566778899aabbccddeeff
ESP32_TTY=/dev/ttyUSB0
ESP32_BAUD=115200
EVSE_MAX_CURRENT_A=200
EVSE_MAX_VOLTAGE_V=920
EOF
  fi
}

step_systemd() {
  log "Installing systemd unit (optional)"
  install -m 0644 "${ROOT}/systemd/everest-hw.service" "${SYSTEMD_DIR}/everest-hw.service"
  systemctl daemon-reload
  systemctl enable everest-hw.service || true
  log "You can start with: systemctl start everest-hw"
}

# Optional: install a prebuilt dist instead of building (same layout as make_dist.sh)
# Usage: USE_DIST=/path/or/url sudo bash setup_native_ubuntu.sh
step_install_prebuilt_dist() {
  local src="$1"
  local tmp=""
  if [[ "$src" =~ ^https?:// ]]; then
    tmp=$(mktemp -d)
    log "Downloading prebuilt dist from URL: $src"
    curl -L "$src" -o "$tmp/dist.tar.gz"
    src="$tmp/dist.tar.gz"
  fi
  if [[ -f "$src" ]]; then
    tmp=$(mktemp -d)
    log "Extracting prebuilt dist tarball: $src"
    tar -xf "$src" -C "$tmp"
    src="$tmp"
  fi
  if [[ ! -d "$src" ]]; then err "Prebuilt dist not found: $src"; exit 2; fi
  if [[ ! -x "$src/bin/manager" ]]; then err "Invalid dist: missing bin/manager"; exit 2; fi
  log "Installing prebuilt dist from $src"
  install -d "${PREFIX}/bin" "${PREFIX}/libexec/everest" "${PREFIX}/etc/everest" "${PREFIX}/share/everest"
  rsync -a --no-owner --no-group "$src/bin/" "${PREFIX}/bin/"
  rsync -a --no-owner --no-group "$src/libexec/everest/" "${PREFIX}/libexec/everest/"
  rsync -a --no-owner --no-group "$src/etc/everest/" "${PREFIX}/etc/everest/" || true
  rsync -a --no-owner --no-group "$src/share/everest/" "${PREFIX}/share/everest/" || true
}

main() {
  require_root
  step_pkgs
  if [[ -n "${USE_DIST:-}" ]]; then
    step_install_prebuilt_dist "${USE_DIST}"
  else
    step_build_everest_core
  fi
  step_install_modules
  step_install_configs
  step_systemd
  log "Setup complete. Edit ${ETC_DIR}/ev.env and ${ETC_DIR}/plc_only.yaml as needed, then: systemctl start everest-hw"
}

main "$@"
