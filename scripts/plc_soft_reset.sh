#!/usr/bin/env bash
set -euo pipefail

echo "[plc-soft-reset] Bringing PLC interface down (if present) ..."
for i in eth1 plc0; do ip link set "$i" down 2>/dev/null || true; done

echo "[plc-soft-reset] Attempting module reload (qcaspi) ..."
if lsmod | grep -q '^qcaspi'; then
  modprobe -r qcaspi qca_7k_common 2>/dev/null || true
  sleep 0.3
fi
# Prefer a stable MAC by default (qcaspi_pluggable=0); allow override via env
modprobe qcaspi qcaspi_clkspeed=${QCASPI_CLKSPEED:-12000000} qcaspi_burst_len=${QCASPI_BURST:-5000} qcaspi_pluggable=${QCASPI_PLUGGABLE:-0} || {
  echo "[plc-soft-reset] modprobe qcaspi failed" >&2; exit 1;
}

echo "[plc-soft-reset] Rebinding SPI device if driver path exists ..."
# Kernel driver is typically 'qcaspi'; some older trees used 'qca7000'.
drv_dir=""
for d in /sys/bus/spi/drivers/qcaspi /sys/bus/spi/drivers/qca7000 /sys/bus/spi/drivers/qca7000-spi; do
  if [ -d "$d" ]; then drv_dir="$d"; break; fi
done
if [ -n "$drv_dir" ] && [ -e "$drv_dir/spi0.0" ]; then
  echo spi0.0 > "$drv_dir/unbind" || true
  sleep 0.1
  echo spi0.0 > "$drv_dir/bind" || true
fi

echo "[plc-soft-reset] Waiting for netdev ..."
for i in {1..20}; do
  if ip link show eth1 >/dev/null 2>&1; then break; fi
  sleep 0.2
done

echo "[plc-soft-reset] Bringing iface up and permissive ..."
ip link set eth1 up 2>/dev/null || true
ip link set eth1 promisc on multicast on allmulticast on 2>/dev/null || true
# Optionally set a static MAC to keep PLC L2 identity stable across resets
if [ -n "${EVSE_PLC_STATIC_MAC:-}" ]; then
  ip link set dev eth1 address "${EVSE_PLC_STATIC_MAC}" 2>/dev/null || true
fi

echo "[plc-soft-reset] ethtool driver stats:"
ethtool -S eth1 || true
echo "[plc-soft-reset] Done."
