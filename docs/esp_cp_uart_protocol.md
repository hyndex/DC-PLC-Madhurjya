ESP32-S3 CP UART Protocol

- Link: ESP32-S3 (pins `GPIO44` RX, `GPIO43` TX) ↔ Raspberry Pi UART
- Serial: `115200 8N1`, newline-delimited JSON messages
- ESP sends periodic status every 200 ms and on command acknowledgements.

Messages from Pi to ESP (one per line):

- {"cmd":"ping"}
- {"cmd":"get_status"}
- {"cmd":"set_pwm","duty":0..100,"enable":true|false}  (manual mode only)
- {"cmd":"enable_pwm","enable":true|false}               (manual mode only)
- {"cmd":"set_freq","hz":500..5000}
- {"cmd":"set_mode","mode":"dc|manual"}

Messages from ESP to Pi:

- Status: {"type":"status","cp_mv":int,"state":"A|B|C|D|E|F","mode":"dc|manual","pwm":{"enabled":bool,"duty":0..100,"hz":int}}
- Pong:   {"type":"pong"}
- Error:  {"type":"error","msg":"..."}

ESP pin mapping (firmware defaults):

- CP PWM: `GPIO38` (1 kHz, 12-bit LEDC)
- CP ADC: `GPIO1` (peak-of-burst in mV; thresholds map A..F)
- UART to Pi: RX `GPIO44`, TX `GPIO43`

Notes

- Thresholds (mV): A≥2300, B≥2000, C≥1700, D≥1450, E≥1250, else F
- Duty range: 0..100 (mapped to 0..4095 LEDC counts)
- DC mode behavior (default):
  - Idle/unplugged (A) and fault (E/F): drive 100% duty to keep CP at +12 V
  - Connected (B/C/D): fixed 5% duty for DC fast charging (per CCS guidance)
- Manual mode: when disabled, firmware holds 100% duty (idle high) to keep +12 V
- Debug USB console (CDC) remains on `Serial` at 115200 baud
- Sampling/noise handling: a short burst (default 64 samples, ~80 µs spacing)
  is captured each cycle. The firmware uses the maximum (upper plateau) from
  this burst to report `cp_mv` and infer A..F state, which reliably captures
  the +12 V plateau under PWM. ~100 mV hysteresis on state transitions prevents
  flapping near thresholds. USB logs also show min/avg for diagnostics.

Logging

- USB-CDC (`Serial`):
  - Human-readable boot/init
  - Periodic status snapshot (1 s cadence): `mv_max`, `mv_min`, `mv_avg`, state, mode, PWM config, applied duty
  - Event logs: CP state transitions, command acks, errors
- UART (to Pi): periodic JSON status frames every 200 ms; commands/acks as specified above
- Recommended: enable Python DEBUG logs to capture UART TX/RX lines (logger `esp.cp`)

Build-time tuning (optional `-D` macros)

- `USB_LOG_PERIOD_MS` (default `1000`): cadence for USB human-readable snapshots
- `CP_SAMPLE_COUNT` (default `64`): samples per burst for plateau capture
- `CP_SAMPLE_DELAY_US` (default `80`): delay between samples within the burst
