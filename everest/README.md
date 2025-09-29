# EVerest PLC-only stack (DC)

Scaffold for running SLAC + ISO 15118-2 DC using EVerest, with a HAL adapter to our ESP32-S3 firmware.

- Modules: `slac`, `evse_v2g` (DC), `esp32_hal_adapter` (Python), `evse_params_provider` (Python)
- Scripts: QCA7005 health/reset adapted from ECO
- Config: `config/plc_only.yaml`
- Env: `.env`

Next steps:
- Add everest-core submodule: `git submodule add https://github.com/EVerest/everest-core everest/everest-core`
- Build and run with the config, wire interfaces to actual everest-core interface names.
