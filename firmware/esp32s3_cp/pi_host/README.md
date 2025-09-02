Pi-side ESP32-S3 Control Pilot client

Overview
- Talks to the ESP32-S3 CP helper over a UART using newline-delimited JSON.
- Implements high-level communication (HLC) phase control and timeouts using the new ESP commands:
  - set_hlc_timeouts, hlc_begin, hlc_keepalive, hlc_end, hlc_abort, hlc_clear_override
- Provides robust read loop, command acknowledgements, auto-keepalive during charging, and reconnection.

Files
- esp_cp_client.py: Library client class `ESPCPClient`.
- cli.py: Simple CLI to exercise commands for local testing.

Usage
1) Library
   from pi_host.esp_cp_client import ESPCPClient, HLCPhase
   client = ESPCPClient(port="/dev/serial0", baudrate=115200)
   client.start()
   client.set_hlc_timeouts(setup_ms=7000, keepalive_ms=4000, terminate_ms=5000)
   client.hlc_begin(HLCPhase.SETUP)
   # ... after HLC negotiation succeeds on PLC layer ...
   client.hlc_begin(HLCPhase.CHARGING)
   # auto-keepalive is enabled by default; or call client.hlc_keepalive()
   status = client.get_status(timeout=1.0)
   print(status)
   client.hlc_end()
   client.stop()

2) CLI
   python3 -m pi_host.cli --port /dev/serial0 status
   python3 -m pi_host.cli --port /dev/serial0 begin setup
   python3 -m pi_host.cli --port /dev/serial0 begin charging
   python3 -m pi_host.cli --port /dev/serial0 keepalive
   python3 -m pi_host.cli --port /dev/serial0 abort
   python3 -m pi_host.cli --port /dev/serial0 clear-override
   python3 -m pi_host.cli --port /dev/serial0 set-timeouts --setup 7000 --keepalive 4000 --terminate 5000

Design notes
- Commands are serialized (one in flight) and matched to acknowledgements by `cmd` echo in the reply.
- The read loop handles status and error events interleaved with acks; events are emitted to subscribers via a callback.
- Auto-keepalive sends `hlc_keepalive` at 70% of the configured keepalive timeout while the phase is CHARGING.
- On ESP timeout errors (hlc_timeout_*), the client surfaces an event and holds off auto-keepalive until the next begin.
- Reconnect: On serial errors, the client attempts reconnection with exponential backoff, and requests a status on reconnect.

Dependencies
- Python 3.8+
- pyserial (install on the Pi): pip install pyserial

Notes
- Adjust the serial port as wired in your hardware (e.g., /dev/ttyS0, /dev/serial0, /dev/ttyAMA1).
- Ensure the ESP is configured for 115200 8N1 on the Pi link (matches firmware).
