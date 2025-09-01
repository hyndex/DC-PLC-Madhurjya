#!/usr/bin/env bash
set -euo pipefail

# This script prepares a Raspberry Pi (Zero/3/4) to run the EVSE stack
# with SLAC + ISO 15118 and the simulation API. It installs system
# dependencies, sets up a Python virtualenv, and installs the local
# iso15118 and pyslac submodules in editable mode to ensure version
# consistency with this repository.

if [ "$(id -u)" != "0" ]; then
  echo "This script must be run as root" 1>&2
  exit 1
fi

usage() {
  cat <<USAGE
Usage: $0 [--use-nm|--no-nm]

Options:
  --use-nm   Force use of NetworkManager for PLC (installs network-manager)
  --no-nm    Force use of dhcpcd for PLC (skips NetworkManager)
  -h, --help Show this help message and exit
USAGE
}

# Parse flags (simple)
FORCE_USE_NM=""
for arg in "$@"; do
  case "$arg" in
    --use-nm) FORCE_USE_NM=1 ;;
    --no-nm)  FORCE_USE_NM=0 ;;
    -h|--help) usage; exit 0 ;;
    *) ;; # ignore unknown for now
  esac
done

REPO_ROOT=$(cd "$(dirname "$0")" && pwd)
VENV_DIR=${VENV_DIR:-/opt/evse-venv}

echo "Initializing and updating Git submodules..."
git -C "${REPO_ROOT}" submodule update --init --recursive

echo "Installing system packages (this may take a while on Pi Zero)..."
export DEBIAN_FRONTEND=noninteractive

# Detect Raspberry Pi OS and decide whether to use/install NetworkManager
IS_RPI_OS=0
if [ -f /etc/os-release ]; then
  if grep -qiE 'raspbian|raspberry pi os' /etc/os-release; then
    IS_RPI_OS=1
  fi
fi

# Decide USE_NM from flags or OS defaults
if [ -n "$FORCE_USE_NM" ]; then
  USE_NM=$FORCE_USE_NM
elif [ "$IS_RPI_OS" -eq 1 ]; then
  USE_NM=0
else
  USE_NM=1
fi

if [ "$USE_NM" -eq 1 ]; then
  NM_PKG="network-manager"
  echo "Using NetworkManager for PLC configuration"
else
  NM_PKG=""
  echo "Using dhcpcd for PLC configuration (no NetworkManager)"
fi

apt-get update
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  libssl-dev \
  libffi-dev \
  libpcap0.8 \
  libpcap0.8-dev \
  git \
  iproute2 \
  iputils-ping \
  net-tools \
  default-jre-headless \
  rustc \
  cargo \
  curl \
  ethtool \
  gpiod ${NM_PKG}

# Persist choice for the post-boot script to read later
mkdir -p /etc/default
echo "USE_NM=${USE_NM}" > /etc/default/plc-post

echo "Ensuring TUN/TAP support (for SLAC/TAP usage)..."
modprobe tun || true

echo "Configuring SPI and QCA7000 overlay..."
IRQ_PIN=${IRQ_PIN:-25}
SPI_SPEED=${SPI_SPEED:-4000000}

# Detect boot config path
BOOTCFG="/boot/firmware/config.txt"
if [ ! -f "$BOOTCFG" ]; then
  BOOTCFG="/boot/config.txt"
fi
if [ ! -f "$BOOTCFG" ]; then
  echo "Unable to find Raspberry Pi boot config (tried /boot/firmware/config.txt and /boot/config.txt)." >&2
  exit 1
fi

echo "Using boot config: $BOOTCFG"
mkdir -p "$(dirname "$BOOTCFG")"

if ! grep -q '^dtparam=spi=on' "$BOOTCFG"; then
  echo "Adding dtparam=spi=on to $BOOTCFG"
  echo 'dtparam=spi=on' | tee -a "$BOOTCFG" >/dev/null
else
  echo "dtparam=spi=on already present"
fi

OVERLAY_LINE="dtoverlay=qca7000,int_pin=${IRQ_PIN},speed=${SPI_SPEED}"
if ! grep -q '^dtoverlay=qca7000' "$BOOTCFG"; then
  echo "Adding $OVERLAY_LINE to $BOOTCFG"
  echo "$OVERLAY_LINE" | tee -a "$BOOTCFG" >/dev/null
else
  # Update parameters if line exists without our params
  if ! grep -q "$OVERLAY_LINE" "$BOOTCFG"; then
    echo "Updating existing qca7000 overlay parameters to int_pin=${IRQ_PIN}, speed=${SPI_SPEED}"
    sed -i "" -e "s/^dtoverlay=qca7000.*/${OVERLAY_LINE}/" "$BOOTCFG" 2>/dev/null || \
    sed -i -e "s/^dtoverlay=qca7000.*/${OVERLAY_LINE}/" "$BOOTCFG" || true
  else
    echo "qca7000 overlay with desired params already present"
  fi
fi

echo "Creating post-boot verification and network setup script..."
cat >/usr/local/sbin/plc_post_boot.sh <<'POST'
#!/usr/bin/env bash
set -euo pipefail
LOG(){ echo "[plc-post] $(date -Iseconds) $*"; }

# Load persisted preference if present; otherwise decide by OS
if [ -f /etc/default/plc-post ]; then
  # shellcheck disable=SC1091
  . /etc/default/plc-post || true
fi
if [ -z "${USE_NM:-}" ]; then
  USE_NM=1
  if [ -f /etc/os-release ]; then
    if grep -qiE 'raspbian|raspberry pi os' /etc/os-release; then
      USE_NM=0
    fi
  fi
fi

LOG "Waiting for qcaspi driver to bind..."
for i in {1..60}; do
  if dmesg | grep -qi '\bqcaspi\b'; then
    break
  fi
  sleep 1
done

LOG "Detecting QCA interface by driver name..."
IFACE=""
for n in /sys/class/net/*; do
  i=$(basename "$n")
  if ethtool -i "$i" 2>/dev/null | grep -qi '^driver: *qcaspi'; then
    IFACE="$i"; break
  fi
done

if [ -z "$IFACE" ]; then
  LOG "ERROR: could not find netdev with driver qcaspi"; ip -o link || true; exit 1
fi
LOG "Detected PLC interface: $IFACE"

if [ "$USE_NM" -eq 1 ] && command -v nmcli >/dev/null 2>&1; then
  if ! nmcli -t -f NAME con show | grep -qx "plc0"; then
    LOG "Creating NetworkManager connection plc0 for $IFACE (DHCP IPv4, IPv6 ignore, never default)"
    nmcli con add type ethernet ifname "$IFACE" con-name plc0 ipv4.method auto ipv6.method ignore || true
  fi
  # Ensure PLC link never becomes default route and has low priority
  LOG "Hardening plc0 connection: never default route, low metric, ignore auto DNS"
  nmcli con modify plc0 ipv4.never-default yes ipv4.route-metric 600 ipv4.ignore-auto-dns yes || true
  LOG "Bringing up connection plc0"
  nmcli con up plc0 || true
else
  LOG "Using dhcpcd to configure $IFACE (either RPi OS or nmcli missing)"
  CONF=/etc/dhcpcd.conf
  if [ -w "$CONF" ]; then
    # Ensure per-interface section exists with safe defaults
    if ! grep -q "^interface $IFACE\b" "$CONF"; then
      LOG "Adding dhcpcd section for $IFACE with nogateway + low metric"
      {
        echo ""
        echo "# Auto-added by plc_post_boot.sh for qcaspi interface"
        echo "interface $IFACE"
        echo "  metric 600"
        echo "  nogateway"
      } | tee -a "$CONF" >/dev/null
    else
      # Update metric/nogateway if missing in existing section
      awk -v IFACE="$IFACE" '
        BEGIN{insec=0}
        {
          if($0 ~ /^interface[[:space:]]+" IFACE "\b/){insec=1; print; next}
          if(insec && $0 ~ /^interface[[:space:]]+/){
            if(!seen_metric) print "  metric 600";
            if(!seen_nogw) print "  nogateway";
            insec=0
          }
          if(insec && $0 ~ /^[[:space:]]+metric[[:space:]]+/){seen_metric=1}
          if(insec && $0 ~ /^[[:space:]]+nogateway\b/){seen_nogw=1}
          print
        }
        END{
          if(insec){
            if(!seen_metric) print "  metric 600";
            if(!seen_nogw) print "  nogateway";
          }
        }
      ' "$CONF" >"${CONF}.tmp" && mv "${CONF}.tmp" "$CONF" || true
    fi
    LOG "Restarting dhcpcd to apply settings"
    systemctl restart dhcpcd || true
    dhcpcd -n "$IFACE" || true
  else
    LOG "dhcpcd.conf not writable; skipping dhcpcd adjustments"
  fi
fi

LOG "Interface details:"
ip -s addr show "$IFACE" || true
LOG "Pinging broadcast to stimulate traffic (may fail on some networks)"
ping -c1 -W1 255.255.255.255 || true
LOG "Done."
POST
chmod +x /usr/local/sbin/plc_post_boot.sh

echo "Creating systemd oneshot to run post-boot script..."
cat >/etc/systemd/system/plc-post-boot.service <<'UNIT'
[Unit]
Description=PLC qca7000 post-boot verification and network setup
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/plc_post_boot.sh

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable plc-post-boot.service >/dev/null 2>&1 || true

echo "Installing optional RESET_L deassert service (BCM24 high at boot)..."
RESET_GPIO=${RESET_GPIO:-24}
cat >/etc/systemd/system/plc-reset.service <<UNIT
[Unit]
Description=PLC reset deassert (BCM${RESET_GPIO} high)
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/bin/gpioset --mode=exit 0 ${RESET_GPIO}=1

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable plc-reset.service >/dev/null 2>&1 || true

if [ ! -d "${VENV_DIR}" ]; then
  echo "Creating Python virtual environment at ${VENV_DIR}..."
  python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
python -V
pip -V

echo "Upgrading pip/setuptools/wheel..."
pip install --upgrade pip setuptools wheel

echo "Installing Python requirements..."
pip install -r "${REPO_ROOT}/requirements.txt"

# Extra runtime deps for iso15118 and tests/utilities
pip install aiofile

echo "Installing local submodules in editable mode..."
# Install iso15118 and pyslac from local sources to keep versions aligned
pip install -e "${REPO_ROOT}/src/iso15118" --no-deps
pip install -e "${REPO_ROOT}/src/pyslac" --no-deps
pip install -r "${REPO_ROOT}/requirements-submodules.txt"

echo "Installing optional packages for simulation/control..."
pip install fastapi "pydantic<2" "httpx<0.28" uvicorn requests

echo "Generating example certificates (PKI) if not present..."
if [ -x "${REPO_ROOT}/scripts/generate_certs.sh" ]; then
  "${REPO_ROOT}/scripts/generate_certs.sh" || true
fi

cat <<'EOF'
Setup complete.

Next steps:
1) The system will reboot now to apply the SPI/overlay changes.

   If it does not reboot automatically, run: sudo reboot

2) After reboot, activate the environment and run one of:

   # Start the simulation API (FastAPI)
   source /opt/evse-venv/bin/activate
   python -m uvicorn src.ccs_sim.fastapi_app:app --host 0.0.0.0 --port 8000

   # Or start the EVSE controller (SLAC + ISO 15118) directly
   # Replace <EVSE_ID> and --iface as needed (e.g., eth0 or wlan0)
   sudo -s
   source /opt/evse-venv/bin/activate
   python src/evse_main.py --evse-id <EVSE_ID> --iface eth0 --controller sim
   # For HAL-based control (pluggable EVSE HAL integration):
   # EVSE_CONTROLLER=hal python src/evse_main.py --evse-id <EVSE_ID> --iface eth0

3) Optional: Run a quick smoke test (API).
   # From another shell on the Pi
   curl -fsS http://localhost:8000/hlc/status || true
   curl -fsS -X POST http://localhost:8000/start_session \
     -H 'Content-Type: application/json' \
     -d '{"target_voltage": 20, "initial_current": 15, "duration_s": 2}'
   curl -fsS http://localhost:8000/status
   curl -fsS http://localhost:8000/meter

Notes:
- Running SLAC/Scapy may require root privileges due to raw sockets.
- Ensure the selected interface exists (`ip link`) and is connected.
- Java runtime is installed for the EXI codec (py4j + EXICodec.jar).
EOF

echo "Setup script completed. Rebooting in 5 seconds..."
sleep 5 || true
reboot
exit 0
