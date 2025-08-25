#!/bin/bash
set -e

# Check if running as root
if [ "$(id -u)" != "0" ]; then
   echo "This script must be run as root" 1>&2
   exit 1
fi

# Initialize and update Git submodules
echo "Initializing and updating Git submodules..."
git submodule update --init --recursive

# Install Python dependencies
echo "Installing Python dependencies..."
apt-get update
apt-get install -y python3-pip
pip3 install -r requirements.txt
pip3 install python-pytuntap
python3 -m pip install iso15118
python3 -m pip install -e src/pyslac --no-deps
python3 -m pip install -r requirements-submodules.txt

cat <<'EOF'
Setup complete.

Please reboot your Raspberry Pi for the changes to take effect:

sudo reboot

After reboot, start the unified EVSE application to initialise the PLC
stack, run SLAC and launch ISO 15118:

sudo python3 src/evse_main.py --evse-id <EVSE_ID> --iface eth0
# or
sudo python3 start_evse.py
EOF

echo "Setup script completed successfully."
exit 0
