#!/usr/bin/env bash
# Native end-to-end setup for Raspberry Pi (DC PLC-only stack)
# - Installs build/runtime deps
# - Builds and installs everest-core (manager)
# - Installs Joulepoint modules (HAL + derate)
# - Installs configs and optional PnC demo certs
# - Installs and enables systemd services

set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
DIST_ROOT_DEFAULT="/opt/everest/everest-core/build/dist"
PREFIX="/usr/local"
ETC_DIR="/etc/everest"
SYSTEMD_DIR="/etc/systemd/system"
JOBS="${JOBS:-$(nproc || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
# Minimal cmake flags to reduce compile time on Pi
# Build only the modules needed for PLC-only DC profile by default
NEEDED_MODULES="EvseSlac;EvseV2G;EvseManager;EvseSecurity"
CMAKE_OPTS_DEFAULT="-DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DCMAKE_RUN_CLANG_TIDY=OFF -DEVEREST_ENABLE_RUN_SCRIPT_GENERATION=OFF -DISO15118_2_GENERATE_AND_INSTALL_CERTIFICATES=OFF -DEVEREST_INCLUDE_MODULES=${NEEDED_MODULES}"
CMAKE_OPTS="${CMAKE_OPTS:-${CMAKE_OPTS_DEFAULT}}"
USE_DIST="${USE_DIST:-}"
SKIP_CORE="${SKIP_CORE:-0}"

log() { printf "[setup-pi] %s\n" "$*"; }
err() { printf "[setup-pi][ERROR] %s\n" "$*" 1>&2; }

have_systemd() {
  command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]
}

require_root() {
  if [[ $(id -u) -ne 0 ]]; then err "Run as root (sudo)."; exit 1; fi
}

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
  log "Building everest-core (manager)"
  cd "${ROOT}/everest-core"
  # Ensure submodules are present (idempotent)
  git submodule update --init --recursive || true
  cmake -B build -S . ${CMAKE_OPTS}
  cmake --build build -j"${JOBS}"
  cmake --install build
  # Sanity: ensure manager is discoverable
  if ! command -v manager >/dev/null 2>&1 && [[ ! -x "/usr/local/bin/manager" ]]; then
    err "manager binary not found after install; check build logs"
  fi
}

step_install_prebuilt_dist() {
  local src="$1"
  local tmp=""
  # Allow URL
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

step_install_modules() {
  log "Installing Joulepoint modules (HAL + derate)"
  install -d "${PREFIX}/libexec/everest/modules/esp32_hal_adapter" \
             "${PREFIX}/libexec/everest/modules/evse_params_provider" \
             "${PREFIX}/libexec/everest/modules/kvs_file_store"
  rsync -a "${ROOT}/modules/esp32_hal_adapter/" "${PREFIX}/libexec/everest/modules/esp32_hal_adapter/"
  rsync -a "${ROOT}/modules/evse_params_provider/" "${PREFIX}/libexec/everest/modules/evse_params_provider/"
  rsync -a "${ROOT}/modules/kvs_file_store/" "${PREFIX}/libexec/everest/modules/kvs_file_store/"
  # Also mirror modules into build dist tree if present (so manager can load from project dist)
  local dist_mods="${ROOT}/everest-core/build/dist/libexec/everest/modules"
  if [[ -d "$dist_mods" ]]; then
    rsync -a "${ROOT}/modules/esp32_hal_adapter/" "$dist_mods/esp32_hal_adapter/" || true
    rsync -a "${ROOT}/modules/evse_params_provider/" "$dist_mods/evse_params_provider/" || true
    rsync -a "${ROOT}/modules/kvs_file_store/" "$dist_mods/kvs_file_store/" || true
  fi
  # Helper scripts used by watchdog/health checks
  install -d "${PREFIX}/libexec/everest/scripts"
  install -m 0755 "${ROOT}/scripts/qca_watchdog.sh" "${PREFIX}/libexec/everest/scripts/qca_watchdog.sh"
  install -m 0755 "${ROOT}/scripts/qca_health.sh" "${PREFIX}/libexec/everest/scripts/qca_health.sh"
  install -m 0755 "${ROOT}/scripts/plc_soft_reset.sh" "${PREFIX}/libexec/everest/scripts/plc_soft_reset.sh"
}

step_install_configs() {
  log "Installing configs under ${ETC_DIR}"
  install -d "${ETC_DIR}"
  # Default hardware and PnC configs
  install -m 0644 "${ROOT}/config/plc_only.yaml" "${ETC_DIR}/plc_only.yaml"
  install -m 0644 "${ROOT}/config/plc_only_pnc.yaml" "${ETC_DIR}/plc_only_pnc.yaml"
  # Create env file if missing
  if [[ ! -f "${ETC_DIR}/ev.env" ]]; then
    cat > "${ETC_DIR}/ev.env" <<EOF
# EVerest PLC-only env
PLC_IFACE=qca0
SLAC_NMK=00112233445566778899aabbccddeeff
ESP32_TTY=/dev/ttyUSB0
ESP32_BAUD=115200
EVSE_MAX_CURRENT_A=200
EVSE_MAX_VOLTAGE_V=920
EOF
  fi
}

step_capabilities() {
  log "Setting CAP_NET_RAW+CAP_NET_ADMIN on SLAC/V2G binaries (if present)"
  local root_dist="${PREFIX}/libexec/everest/modules"
  for bin in \
    "${root_dist}/EvseSlac/EvseSlac" \
    "${root_dist}/EvseV2G/EvseV2G"; do
    if [[ -x "$bin" ]]; then
      setcap cap_net_raw,cap_net_admin=eip "$bin" 2>/dev/null || true
    fi
  done
}

step_enable_ipv6_plc() {
  local ifc="${PLC_IFACE:-${IFACE:-eth1}}"
  log "Ensuring IPv6 enabled on ${ifc} and link-local present"
  install -d /etc/sysctl.d
  cat > /etc/sysctl.d/everest-plc.conf <<EOF
net.ipv6.conf.all.disable_ipv6 = 0
net.ipv6.conf.${ifc}.disable_ipv6 = 0
EOF
  sysctl -p /etc/sysctl.d/everest-plc.conf >/dev/null 2>&1 || true
  ip link set dev "$ifc" up 2>/dev/null || true
  if ! ip -6 addr show dev "$ifc" | grep -q 'fe80::'; then
    ip -6 addr add fe80::2/64 dev "$ifc" scope link 2>/dev/null || true
  fi
}

step_stage_pnc() {
  local dist_root="${DIST_ROOT:-${DIST_ROOT_DEFAULT}}"
  local src_dir="${dist_root}/etc/everest/certs"
  if [[ -d "${src_dir}" ]]; then
    log "Staging demo PnC certs from ${src_dir} to ${ETC_DIR}/certs"
    install -d "${ETC_DIR}/certs"
    rsync -a --delete "${src_dir}/" "${ETC_DIR}/certs/"
  else
    log "No demo PnC certs found at ${src_dir}; skipping"
  fi
}

step_systemd() {
  log "Installing systemd units"
  install -m 0644 "${ROOT}/systemd/everest-hw.service" "${SYSTEMD_DIR}/everest-hw.service"
  # Optional: watchdog service if desired
  if [[ -f "${ROOT}/systemd/qca-watchdog.service" ]]; then
    install -m 0644 "${ROOT}/systemd/qca-watchdog.service" "${SYSTEMD_DIR}/qca-watchdog.service"
  fi
  if have_systemd; then
    systemctl daemon-reload || true
    systemctl enable everest-hw.service || true
    log "You can now start with: systemctl start everest-hw"
  else
    log "systemd not active in this environment; skipping enable/reload"
  fi
}

step_plc_driver_note() {
  cat <<'NOTE'
[INFO] PLC (QCA7005) driver setup note:
- Ensure the qcaspi driver is available so the PLC netdev (e.g., qca0) appears.
- On Raspberry Pi OS, enable SPI overlays and confirm IRQ wiring; consult your board docs.
- After the driver loads, validate with: ip -6 a show dev qca0
- Health check: run everest/scripts/qca_health.sh and optionally enable the watchdog service.
NOTE
}

main() {
  require_root
  if [[ "${SKIP_PKGS:-0}" != "1" ]]; then
    step_pkgs
  else
    log "Skipping package installation as requested"
  fi
  if [[ -n "$USE_DIST" ]]; then
    step_install_prebuilt_dist "$USE_DIST"
  elif [[ "$SKIP_CORE" != "1" ]]; then
    step_build_everest_core
  else
    log "Skipping everest-core build as requested"
  fi
  step_install_modules
  step_install_configs
  # Create state dir for local kvs module and other runtime files
  install -d /var/lib/everest
  step_stage_pnc || true
  step_systemd
  step_capabilities || true
  step_enable_ipv6_plc || true
  step_plc_driver_note
  log "Setup complete. Edit ${ETC_DIR}/ev.env and ${ETC_DIR}/plc_only.yaml as needed, then: systemctl start everest-hw"
}

main "$@"
