# EVerest PLC-only (DC) — Native Raspberry Pi Setup

This project runs SLAC + ISO 15118-2 DC using EVerest with a HAL adapter to our ESP32-S3 firmware.

- Modules: `slac`, `evse_v2g` (DC), `esp32_hal_adapter` (Python), `evse_params_provider` (Python)
- Config: `everest/config/plc_only.yaml` (EIM) and `everest/config/plc_only_pnc.yaml` (PnC)
- Scripts: health/reset/watchdog for QCA7005

Use this guide to install and run natively on a Raspberry Pi (no Docker). It is end-to-end and includes edge cases and validation steps.

## What You Need
- Raspberry Pi 4/5 with 64‑bit Raspberry Pi OS (Bookworm) or Ubuntu 22.04/24.04 (x86_64/arm64)
- Root access (`sudo`)
- Hardware mode: QCA7005 PLC via SPI (`qca7000/qcaspi`) and ESP32‑S3 on `/dev/ttyACM0` or `/dev/ttyUSB0`
- For Ubuntu without hardware, you can still build and run `manager --check` or use Docker for simulation

## Quick Start (Automated)
- Ubuntu 22.04/24.04:
  - `sudo bash everest/scripts/setup_native_ubuntu.sh`
  - Optional: start service `sudo systemctl start everest-hw`; logs `sudo journalctl -u everest-hw -e -f`
- Raspberry Pi (Bookworm):
  - `sudo bash everest/scripts/setup_native_pi.sh`
  - Start service: `sudo systemctl start everest-hw`
  - Logs: `sudo journalctl -u everest-hw -e -f`

### Prebuild and Ship (No compile on the target)
- Create a dist tarball on a machine of the same architecture:
  - `bash everest/scripts/make_dist.sh` (outputs path, e.g., `/tmp/everest-dist-ARCH-*.tar.gz`)
- Install that dist on the target:
  - Ubuntu: `sudo USE_DIST=/path/to/everest-dist-ARCH.tar.gz bash everest/scripts/setup_native_ubuntu.sh`
  - Pi: `sudo USE_DIST=/path/to/everest-dist-arm64.tar.gz bash everest/scripts/setup_native_pi.sh`
  - You can also pass an https URL in `USE_DIST`.

This installs dependencies, builds and installs `manager`, installs our modules/configs into system paths, and enables a systemd unit. It also stages demo PnC certificates if available from the everest-core install.

## Manual Steps (Details and Edge Cases)

### 1) Enable PLC Driver (qcaspi)
- Ensure SPI is enabled and the QCA7000 overlay is loaded. Edit the boot config (path may vary by OS):
  - `sudo nano /boot/firmware/config.txt` (Bookworm) or `sudo nano /boot/config.txt`
- Add/ensure lines (adjust parameters for your HAT/board wiring):
  - `dtparam=spi=on`
  - `dtoverlay=qca7000,irq=<GPIO>,speed=8000000,burst_len=5000,pluggable=1`
- Reboot and check:
  - `lsmod | grep qcaspi`
  - `ip link` (look for `qca0`/`plc0`)
  - `ethtool -i qca0` (driver `qcaspi`)
  - `everest/scripts/qca_health.sh`

Notes:
- GPIO/IRQ values are board‑specific. Consult your HAT datasheet.
- If the overlay is missing, install Raspberry Pi firmware packages or update your OS.
- If the interface name isn’t `qca0`, use the actual name in your env/config.

### 2) Build and Install EVerest Core (Ubuntu + Pi)
- Automated scripts pass minimal flags to reduce build time on Pi/low‑power machines.
- Manual steps if preferred:
  - Ensure submodules: `git submodule update --init --recursive`
  - Install build tools: `sudo apt-get update && sudo apt-get install -y cmake build-essential libssl-dev libboost-all-dev libpcap-dev libevent-dev libcap-dev libsqlite3-dev python3 python3-pip python3-venv ethtool`
  - Build/install manager with minimal flags:
    - `cd everest/everest-core`
    - `cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DCMAKE_RUN_CLANG_TIDY=OFF -DEVEREST_ENABLE_RUN_SCRIPT_GENERATION=OFF -DISO15118_2_GENERATE_AND_INSTALL_CERTIFICATES=OFF`
    - `cmake --build build -j$(nproc)`
    - `sudo cmake --install build`
- Verify:
  - `which manager` or check `/usr/local/bin/manager`
  - `manager --help` (prints usage)

### 3) Install Our Modules and Configs
- The setup script installs to system locations:
  - Modules: `/usr/local/libexec/everest/modules/{esp32_hal_adapter,evse_params_provider}`
  - Configs: `/etc/everest/plc_only.yaml` and `/etc/everest/plc_only_pnc.yaml`
  - Env file: `/etc/everest/ev.env` (created if missing; edit it)
  - Watchdog/health scripts: `/usr/local/libexec/everest/scripts/*`
- Edit the env file to match your hardware:
  - `PLC_IFACE=qca0` (or your actual PLC interface)
  - `SLAC_NMK=00112233445566778899aabbccddeeff` (32 hex chars, no separators)
  - `ESP32_TTY=/dev/ttyACM0` or `/dev/ttyUSB0`
  - `ESP32_BAUD=115200`
  - `EVSE_MAX_CURRENT_A=200` and `EVSE_MAX_VOLTAGE_V=920` (adjust to hardware limits)

### 4) Optional: Enable PLC Watchdog
- We provide a watchdog that soft‑resets the PLC when health degrades.
- Install step already placed the unit; enable it if desired:
  - `sudo systemctl enable --now qca-watchdog`
  - Logs: `sudo journalctl -u qca-watchdog -e -f`

## Running EVerest (Native)

### Option A: systemd (recommended)
- Start: `sudo systemctl start everest-hw`
- Enable at boot: `sudo systemctl enable everest-hw`
- Logs: `sudo journalctl -u everest-hw -e -f`

### Option B: Foreground (manual)
- Use our native runner (auto‑loads env from `/etc/everest/ev.env` if present):
  - `bash everest/scripts/run_hw_native.sh`
- Override config/env:
  - `ENV_FILE=everest/.env CONFIG=everest/config/plc_only.yaml bash everest/scripts/run_hw_native.sh`

### Validate Before First Run
- Confirm PLC IPv6 link‑local exists (required for ISO 15118):
  - `ip -6 addr show dev qca0 | grep 'scope link'`
  - If missing, ensure IPv6 is enabled and bring interface up:
    - `sudo sysctl -w net.ipv6.conf.all.disable_ipv6=0`
    - `sudo ip link set qca0 up`
    - `sudo ip -6 addr add fe80::1234/64 dev qca0 scope link` (temporary LL for validation)
- Confirm serial device exists and is free:
  - `ls -l /dev/ttyACM0` or `/dev/ttyUSB0`
  - `lsof /dev/ttyACM0` should be empty

## Configuration Notes
- Main wiring: `everest/config/plc_only.yaml`
  - `EvseSlac` → `EvseManager (DC)` ← `EvseV2G (ISO 15118‑2)`
  - HAL provides `evse_board_support` and `power_supply_DC`
- For PnC:
  - Use `/etc/everest/plc_only_pnc.yaml` or set `tls_security: force`
  - Demo certs auto‑staged when available; otherwise place under `/etc/everest/certs/` and configure `EvseSecurity`

## Troubleshooting and Edge Cases
- Faster builds on Pi
  - Use minimal flags (already in our scripts). You can further lower parallel jobs via `JOBS=2 CMAKE_OPTS="..."` env.
  - Avoid building on Pi by installing a prebuilt `dist` from another arm64 machine: `sudo USE_DIST=/path/to/everest-dist-arm64.tar.gz bash everest/scripts/setup_native_pi.sh` (also accepts an https:// URL).
  - If you must build everything on Pi, increase swap (see below) and use fewer jobs, e.g., `JOBS=2`.
- Manager not found after build
  - Re‑run install and check logs: `cmake --install build`
  - Ensure `/usr/local/bin` is in `PATH`
- PLC driver/interface missing
  - Verify overlay and SPI: see section above
  - `lsmod | grep qcaspi`, `dmesg | grep -i qca`, `ethtool -i <iface>`
  - Run health script: `everest/scripts/qca_health.sh`
- No IPv6 link‑local on PLC
  - Enable IPv6 and add LL address (commands in validation section)
  - Some distros disable IPv6 by default; verify `sysctl` values
- Serial permission denied
  - Run as root (systemd unit does)
  - Or add your user to `dialout` and re‑login: `sudo usermod -aG dialout $USER`
- SLAC NMK format errors
  - Must be exactly 32 hex chars; remove separators
- Build runs out of RAM on Pi
  - Increase swap: `sudo dphys-swapfile swapoff; sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile; sudo dphys-swapfile setup; sudo dphys-swapfile swapon`
  - Reduce jobs: `JOBS=2 sudo bash everest/scripts/setup_native_pi.sh`
- ISO 15118 errors or bind failures
  - Ensure PLC iface has IPv6 LL and is up
  - Check that UDP 15118 is free: `sudo ss -ulpn | grep 15118`
- PnC cryptography or time errors
  - Verify system clock/NTP and certificate validity period
  - Place correct certs under `/etc/everest/certs/`; set `tls_security: force`
- Watchdog doesn’t act
  - Check its logs; it triggers after consecutive health failures. Tune via `SLEEP_OK`, `SLEEP_FAIL`, `MAX_FAILS` env vars (set in the service or `/etc/everest/ev.env`).

## Uninstall / Cleanup
- Stop and disable services:
  - `sudo systemctl disable --now everest-hw qca-watchdog`
- Remove installed files:
  - `sudo rm -rf /usr/local/libexec/everest /etc/everest`
  - Optionally remove `everestpy` wheels installed by everest-core if you need a clean Python env

## Docker (Optional)
- For development/simulation, Docker compose definitions are in `everest/docker`. Native is recommended for Pi hardware.
