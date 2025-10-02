# EVerest PLC-only (DC) — Native Raspberry Pi Setup

This project runs SLAC + ISO 15118-2 DC using EVerest with a HAL adapter to our ESP32-S3 firmware.

- Modules: `slac`, `evse_v2g` (DC), `esp32_hal_adapter` (Python), `evse_params_provider` (Python)
- Config: `everest/config/plc_only.yaml` (EIM) and `everest/config/plc_only_pnc.yaml` (PnC)
- Scripts: health/reset/watchdog for QCA7005

Use this guide to install and run natively on a Raspberry Pi (no Docker). It is end-to-end and includes edge cases and validation steps.

## HAL Adapter Compatibility & Validation

This project integrates a Python HAL adapter that bridges EVerest to our ESP32‑S3 peripheral over UART (JSON‑RPC). Below is a quick compatibility map between the adapter and the EVerest interfaces, validation steps, and how we solved issues found during bring‑up on a Raspberry Pi 4B.

### What The HAL Publishes/Implements

- `evse_board_support` (provides)
  - Publishes
    - `capabilities` → evse_board_support/HardwareCapabilities
      - Fields set: `max_current_A_import`, `min_current_A_import`, `max_phase_count_import`, `min_phase_count_import`, `max_current_A_export`, `min_current_A_export`, `max_phase_count_export`, `min_phase_count_export`, `supports_changing_phases_during_charging`, `connector_type`
    - `telemetry` → evse_board_support/Telemetry
      - `evse_temperature_C`, `plug_temperature_C`, `fan_rpm`, `supply_voltage_12V`, `supply_voltage_minus_12V`, `relais_on`
    - `event` → board_support_common/BspEvent
      - Values: `A|B|C|D|E|F|PowerOn|PowerOff|EvseReplugStarted|EvseReplugFinished`
  - Implements commands
    - `enable(bool)`; `pwm_on(value:number 0..100)`; `pwm_off()`; `pwm_F()`
    - Stubs (DC only, no effect): `ac_read_pp_ampacity`, `ac_set_overcurrent_limit_A`, `ac_switch_three_phases_while_charging`, `evse_replug`, `allow_power_on`

- `power_supply_DC` (provides)
  - Publishes
    - `capabilities` → power_supply_DC/Capabilities (export limits, ripple/tolerance etc.)
    - `voltage_current` → power_supply_DC/VoltageCurrent
      - Fields: `voltage_V`, `current_A`
  - Implements commands
    - `setMode(mode, phase)`; `setExportVoltageCurrent(voltage, current)`; `setImportVoltageCurrent(voltage, current)` (no‑op for our HW)

Adapter details: see `everest/modules/esp32_hal_adapter/`.

### How We Verified Compatibility

- MQTT inspection (broker on `localhost:1883`):
  - HAL topics follow `everest/<module_id>/<provided_interface>/<variable>`
    - Examples
      - `everest/esp32_hal_adapter/evse_board_support/capabilities`
      - `everest/esp32_hal_adapter/evse_board_support/event`
      - `everest/esp32_hal_adapter/power_supply_DC/voltage_current`
  - Commands can be driven via EVerest generated RPC or through EvseManager flow; for raw tests use module RPC or the controller API.

- Controller/manager logs:
  - `journalctl -u everest-hw -e -f` (systemd) or foreground logs via `everest/scripts/run_hw_native.sh`.

If messages do not appear on MQTT:
- Confirm the serial device exists and is free: `ls -l /dev/ttyACM0 && lsof /dev/ttyACM0`
- Check HAL init: log shows `Module esp32_hal_adapter initialized`
- The adapter attempts a periodic `cp_get_status`; without an ESP connected, telemetry/events will be absent and you’ll see serial open errors.

### Payload Examples (what you should see)

- `evse_board_support/capabilities`
  ```json
  {
    "max_current_A_import": 200,
    "min_current_A_import": 0,
    "max_phase_count_import": 1,
    "min_phase_count_import": 1,
    "max_current_A_export": 0,
    "min_current_A_export": 0,
    "max_phase_count_export": 1,
    "min_phase_count_export": 1,
    "supports_changing_phases_during_charging": false,
    "connector_type": "IEC62196Type2Socket"
  }
  ```

- `evse_board_support/telemetry`
  ```json
  {
    "evse_temperature_C": 30.0,
    "plug_temperature_C": 30.0,
    "fan_rpm": 0,
    "supply_voltage_12V": 12.0,
    "supply_voltage_minus_12V": -12.0,
    "relais_on": false
  }
  ```

- `evse_board_support/event`
  ```json
  { "event": "A" }
  ```

- `power_supply_DC/capabilities`
  ```json
  {
    "bidirectional": false,
    "current_regulation_tolerance_A": 1.0,
    "peak_current_ripple_A": 1.0,
    "max_export_voltage_V": 920,
    "min_export_voltage_V": 0,
    "max_export_current_A": 200,
    "min_export_current_A": 0,
    "max_export_power_W": 184000
  }
  ```

- `power_supply_DC/voltage_current`
  ```json
  { "voltage_V": 0.0, "current_A": 0.0 }
  ```

### Command Argument Compatibility Notes

- `evse_board_support.pwm_on` → EVerest expects `{ "value": <0..100> }`. The HAL also accepts a legacy `{ "duty_cycle": <0..100> }` for convenience.
- `power_supply_DC.setExportVoltageCurrent` → EVerest uses `{ "voltage": <V>, "current": <A> }`. The HAL also accepts fallback keys `voltage_V`/`current_A`.

## What We Changed During Bring‑Up (Pi 4B)

Only targeted changes were made to avoid long rebuilds and to keep scope minimal:

- Build/Configuration
  - Pulled submodule `everest-core` and built only required modules (`EvseSlac;EvseV2G;EvseManager;EvseSecurity`).
  - Minor CMake resilience: avoid creating an alias `SDBusCpp::sdbus-c++` too early (guards added) to let CPM fetch sdbus‑c++ cleanly.
  - Fixed a harmless `maybe-uninitialized` warning treated as error in `socket_can_handler.cpp`.

- Native install scripts (Pi)
  - `everest/scripts/setup_native_pi.sh`
    - Ensures IPv6 is enabled on the PLC iface (default `eth1`) and guarantees a link‑local if none exists.
    - Applies `setcap cap_net_raw,cap_net_admin=eip` on SLAC/V2G binaries so raw sockets can open without running everything as root.
    - Installs our HAL adapter and a minimal JSON‑file KVS (`kvs_file_store`) to back optional persistence.
  - `everest/scripts/run_hw_native.sh`
    - Pre‑flight check: brings PLC iface up, ensures IPv6 link‑local, and re‑applies capabilities on binaries if missing.

- Runtime config (`/etc/everest/plc_only.yaml`)
  - Set `device: eth1` for SLAC/V2G, `tty: /dev/ttyACM0` for HAL.
  - Added `kvs_file_store` and wired it to `EvseManager.store` (path `/tmp/everest-kvs.json` by default).
  - Removed `evse_params_provider` which caused a clean exit during boot on this image.

## Issues Encountered and Fixes

1) SLAC couldn’t open raw socket (Operation not permitted)
   - Root cause: missing capabilities on SLAC binary.
   - Fix: `setcap cap_net_raw,cap_net_admin=eip` on `EvseSlac` (and optionally `EvseV2G`). Automated in scripts.

2) V2G bind() failed: Address family not supported / no IPv6 link‑local
   - Root cause: IPv6 disabled (or iface up without LL).
   - Fix: enable IPv6 globally and for PLC iface; if needed add `fe80::/64` LL. Automated in scripts.

3) Manager crash `std::future_error: Promise already satisfied`
   - Observed immediately after “No powermeter value received yet!”.
   - Fix applied: removed `evse_params_provider` from PLC‑only profile; manager runs stable. If you need external derating, re‑add it after basic end‑to‑end tests are stable.

4) `ESP periph serial open failed`
   - Root cause: wrong/missing `ESP32_TTY` or device busy.
   - Fix: set `tty: /dev/ttyACM0`, ensure user in `dialout`, and confirm no other process holds the port.

5) Certificates warning
   - `EvseSecurity` creates defaults when CSMS bundle is missing. OK for local testing; configure real CA bundle for PnC.

## How To Validate End‑to‑End

- PLC NIC health
  - `everest/scripts/qca_health.sh` and/or `ip -6 addr show dev eth1`

- Start in foreground
  - `PYTHONPATH="$(pwd)/everest/everest-core/build/dist/lib/everest/everestpy" bash everest/scripts/run_hw_native.sh`

- MQTT topics (examples)
  - `mosquitto_sub -v -t 'everest/esp32_hal_adapter/#'`
  - `mosquitto_sub -v -t 'everest/evse_manager/#'`

- HAL sanity without an EV
  - You should see `capabilities` and `telemetry` published shortly after boot.
  - `event` may stay at `A` until CP state changes.

## Next Tests & Edge Cases To Cover

- SLAC matching with an actual vehicle or simulator; verify EV MAC learned and V2G session starts.
- CP state transitions A→B→C… and replug sequences; ensure `event` stream aligns with mechanical state.
- PSU commands: drive `setExportVoltageCurrent` across a range and verify `voltage_current` tracking and EvseManager current budgeting.
- HAL resilience: unplug/replug ESP32 while running; confirm auto‑reconnect and safe defaults.
- PLC watchdog/soft reset paths under heavy traffic; confirm IPv6 LL remains stable and SLAC recovers.
- Re‑enable `evse_params_provider` (external derating) once base path is stable; ensure it does not cause early termination on this image.

## One‑liners To Reapply Critical Fixes

- Capabilities
  - `sudo setcap cap_net_raw,cap_net_admin=eip /usr/local/libexec/everest/modules/EvseSlac/EvseSlac`

- IPv6 enablement for PLC iface
  - `echo 0 | sudo tee /proc/sys/net/ipv6/conf/all/disable_ipv6`
  - `echo 0 | sudo tee /proc/sys/net/ipv6/conf/eth1/disable_ipv6`
  - `sudo ip link set eth1 up; ip -6 addr show dev eth1 | grep fe80 || sudo ip -6 addr add fe80::2/64 dev eth1`

If you want these baked in automatically, (re)run `sudo bash everest/scripts/setup_native_pi.sh`.

## Firmware Protocol Compatibility (ESP32‑S3 main.cpp)

The ESP32 firmware under `firmware/esp32s3_cp/src/main.cpp` implements a newline‑delimited JSON protocol over UART. The HAL speaks the same protocol.

- JSON‑RPC requests (`{"type":"req","id":"…","method":"…","params":{…}}`) implemented by firmware and used by the HAL:
  - `sys.ping` → keepalive
  - `sys.info` → mode `hw|sim`, capabilities `["contactor","dc","meter","cp"]`, thresholds
  - `sys.arm` → arm window (optional)
  - `dc.enable {on}` → on/off intent
  - `dc.set {v,i[,p_w|p_kw]}` → voltage/current target (HAL maps EVerest `setExportVoltageCurrent` to this)
  - `dc.status` → state and last setpoints
  - `meter.read` → `{v,i,p,e}` (HAL maps to `power_supply_DC/voltage_current` + cached energy)
  - `contactor.set {on}` / `contactor.check` → optional direct contactor control

- CP helper (plain JSON with `{"cmd":"…"}`) implemented by firmware and used by HAL:
  - `get_status` → `{"type":"status","cp_mv","cp_mv_robust","state":"A..F","mode":"dc|manual", "dc":{…}}`
  - `set_mode {mode: "dc"|"manual"}` → switch DC auto vs PWM manual
  - `set_pwm {duty, enable?}` → duty in percent, optional enable; HAL exposes as `pwm_on(value)`/`pwm_off()`
  - `cp.set_thresholds` / `cp.auto_cal` → optional calibration helpers

- Events emitted by the firmware and handled by the HAL:
  - JSON‑RPC event `{"type":"evt","method":"evt:contactor.change","result":{"on":bool,"aux_ok":bool}}`
    - HAL publishes `evse_board_support/event` as `PowerOn`/`PowerOff` on this notification.
  - Periodic `status` frames contain CP state and PWM status (HAL tolerates missing `pwm{}` and defaults safely).

Minor notes
- The HAL tolerates both legacy and canonical field names in commands (`value` vs `duty_cycle`, `voltage/current` vs `voltage_V/current_A`).
- Contactors toggled by the DC state machine may not raise a `evt:contactor.change` event unless controlled via `contactor.set`; this is fine for the PLC‑only profile where EvseManager gates power via HLC phases.


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
- By default, only the core modules required for PLC‑only DC are compiled: `EvseSlac;EvseV2G;EvseManager;EvseSecurity` (set via `-DEVEREST_INCLUDE_MODULES`).
- Manual steps if preferred:
  - Ensure submodules: `git submodule update --init --recursive`
  - Install build tools: `sudo apt-get update && sudo apt-get install -y cmake build-essential libssl-dev libboost-all-dev libpcap-dev libevent-dev libcap-dev libsqlite3-dev python3 python3-pip python3-venv ethtool`
  - Build/install manager with minimal flags:
    - `cd everest/everest-core`
    - `cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DCMAKE_RUN_CLANG_TIDY=OFF -DEVEREST_ENABLE_RUN_SCRIPT_GENERATION=OFF -DISO15118_2_GENERATE_AND_INSTALL_CERTIFICATES=OFF -DEVEREST_INCLUDE_MODULES=EvseSlac;EvseV2G;EvseManager;EvseSecurity`
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
### Building Additional Modules
- To include more EVerest modules (e.g., OCPP201), extend `EVEREST_INCLUDE_MODULES`:
  - Example: `CMAKE_OPTS="-DEVEREST_INCLUDE_MODULES=EvseSlac;EvseV2G;EvseManager;EvseSecurity;OCPP201" bash everest/scripts/build_everest_core.sh`
- To build everything (slower): remove the flag or set an empty include list.
