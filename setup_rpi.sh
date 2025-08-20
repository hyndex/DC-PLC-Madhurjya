#!/bin/bash
set -e

# Check if running as root
if [ "$(id -u)" != "0" ]; then
   echo "This script must be run as root" 1>&2
   exit 1
fi

# Enable SPI and QCA7000 overlay
reboot_required=0

# Enable SPI if not already enabled
if ! grep -q "^dtparam=spi=on" /boot/config.txt; then
    echo "Enabling SPI interface..."
    echo "dtparam=spi=on" >> /boot/config.txt
    reboot_required=1
else
    echo "SPI interface is already enabled."
fi

# Configure QCA7000 overlay
if ! grep -q "^dtoverlay=qca7000,int_pin=23,speed=12000000" /boot/config.txt; then
    echo "Configuring QCA7000 overlay..."
    echo "dtoverlay=qca7000,int_pin=23,speed=12000000" >> /boot/config.txt
    reboot_required=1
else
    echo "QCA7000 overlay is already configured."
fi

if [ $reboot_required -eq 1 ]; then
    echo "SPI interface and QCA7000 overlay configured. A reboot is required for changes to take effect."
else
    echo "SPI interface and QCA7000 overlay already configured."
fi

# Install Python dependencies
echo "Installing Python dependencies..."
apt-get update
apt-get install -y python3-pip
pip3 install -r requirements.txt
pip3 install python-tuntap

# Initialize and update Git submodules
echo "Initializing and updating Git submodules..."
git submodule update --init --recursive

echo "
Setup complete.

Please reboot your Raspberry Pi for the changes to take effect:

sudo reboot

After reboot, you can run the PLC to TUN bridge:

sudo python3 src/plc_communication/plc_to_tun.py
"

echo "Setup script completed successfully."
