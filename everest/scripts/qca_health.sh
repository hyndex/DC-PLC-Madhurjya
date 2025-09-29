#!/usr/bin/env bash
set -euo pipefail

echo "[qca-health] Kernel module info (qcaspi):"
modinfo qcaspi 2>/dev/null || echo "qcaspi module info not available"

BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
echo
echo "[qca-health] Boot config (${BOOTCFG}):"
if [ -f "$BOOTCFG" ]; then
  grep -E '^(dtparam=spi=on|dtoverlay=qca7000.*)' "$BOOTCFG" || true
else
  echo "Boot config not found"
fi

echo
echo "[qca-health] dtoverlay help (qca7000):"
dtoverlay -h qca7000 2>/dev/null || echo "dtoverlay help not available"

echo
echo "[qca-health] dmesg (qca/qcaspi):"
dmesg | grep -i -e qca -e qcaspi || true

echo
echo "[qca-health] Interfaces with qcaspi driver:"
found=0
for n in /sys/class/net/*; do
  i=$(basename "$n")
  if ethtool -i "$i" 2>/dev/null | grep -qi '^driver: *qcaspi'; then
    found=1
    echo "- $i"
    echo "  ethtool -i:"; ethtool -i "$i" || true
    echo "  ip -s link:"; ip -s link show "$i" || true
    echo "  ethtool -S (driver stats):"; ethtool -S "$i" || true
  fi
done
if [ "$found" -eq 0 ]; then
  echo "(none)"
fi

echo
echo "[qca-health] Done."
