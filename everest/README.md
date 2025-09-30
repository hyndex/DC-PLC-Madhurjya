# EVerest PLC-only stack (DC)

Scaffold for running SLAC + ISO 15118-2 DC using EVerest, with a HAL adapter to our ESP32-S3 firmware.

- Modules: `slac`, `evse_v2g` (DC), `esp32_hal_adapter` (Python), `evse_params_provider` (Python)
- Scripts: QCA7005 health/reset adapted from ECO
- Config: `config/plc_only.yaml`
- Env: `.env`

Next steps (end-to-end):

1) Build and install everest-core runtime
   - Submodule already added under `everest/everest-core`
   - Build and install `everestd`:
     - `bash everest/scripts/build_everest_core.sh`

2) Prepare environment
   - Edit `everest/.env` to match your hardware:
     - `ESP32_TTY` (e.g., `/dev/ttyUSB0`), `ESP32_BAUD`
     - `PLC_IFACE` (QCA7005 netdev, e.g., `qca0`)
     - `SLAC_NMK` (32 hex chars), DC limits
   - QCA watchdog (optional): `docker compose -f everest/docker/docker-compose.yml up qca_watchdog`

3) Run EVerest PLC-only stack
   - If `everestd` is in PATH: `everest/scripts/run_plc_only.sh`
   - Or via Docker compose (host network & privileged):
     - `docker compose -f everest/docker/docker-compose.yml up everest`

4) HAL adapter
   - Python HAL module at `modules/esp32_hal_adapter` bridges to the ESP32-S3 JSON-RPC UART protocol
   - Publishes board support and DC power supply capabilities and measurements
   - Handles: PWM on/off, EVSE enable, allow_power_on, DC enable, voltage/current setpoints

5) Configuration
   - Main wiring: `everest/config/plc_only.yaml`
   - Modules: `EvseSlac` → `EvseManager (DC)` ← `EvseV2G (ISO15118-2)`; HAL provides `evse_board_support` and `power_supply_DC`

6) Certificates (optional, PnC)
   - Switch `tls_security` from `prohibit` to `allow`/`force` in `plc_only.yaml`
   - Place certs/keys in `everest/certs/` and configure EvseSecurity module accordingly (see everest-core docs)

Run
- Mac (simulation):
  - Build: `docker build -t everest-plc -f everest/docker/Dockerfile everest`
  - Run: `EVEREST_CONFIG=/opt/everest/config/plc_only_sim.yaml docker compose -f everest/docker/docker-compose.yml up --build everest`
- Linux (hardware):
  - Set `everest/.env` (PLC_IFACE, SLAC_NMK, ESP32_TTY, limits)
  - Run: `docker compose -f everest/docker/docker-compose.yml --profile hw up --build everest-hw`
  - Map serial if needed: add a `devices:` mapping for your TTY

Production Hardening
- QCA7005: EvseSlac `link_status_detection: true` (enabled), `set_key_timeout_ms: 1000`, optional `do_chip_reset`
- Watchdog: `everest/scripts/qca_watchdog.sh` to soft-reset PLC on health failures
- TLS/PnC: set `evse_v2g.tls_security` to `allow|force`; install certs under `everest/certs/`
- Derating: `evse_params_provider` pushes `dc_external_derate` from env
- HAL: phase-aware DC enable (CableCheck/PreCharge gated by `allow_power_on`)

Troubleshooting
- Ensure QCA7005 interface is up and has IPv6 link-local; use `everest/scripts/qca_health.sh`
- Watchdog resets PLC via `plc_soft_reset.sh` when health degrades
- Verify ESP32 port is accessible and not locked by other processes
- Increase logs by exporting `EVSE_LOG_LEVEL=DEBUG`
- If manager complains about manifests, rebuild Docker image without cache:
  - `docker build --no-cache -t everest-plc -f everest/docker/Dockerfile everest`
  - Validate: `docker run --rm -v "$PWD/everest/config":/opt/everest/config everest-plc /opt/everest/everest-core/build/dist/bin/manager --check --config /opt/everest/config/plc_only.yaml`
