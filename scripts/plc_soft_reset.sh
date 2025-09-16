#!/usr/bin/env bash
set -euo pipefail

echo "[plc-soft-reset] Bringing PLC interface down (if present) ..."
for i in eth1 plc0; do ip link set "$i" down 2>/dev/null || true; done

echo "[plc-soft-reset] Attempting module reload (qcaspi) ..."
if lsmod | grep -q '^qcaspi'; then
  modprobe -r qcaspi qca_7k_common 2>/dev/null || true
  sleep 0.3
fi
# Prefer pluggable=1 by default to tolerate hot-plug/reset; allow override via env
modprobe qcaspi qcaspi_clkspeed=${QCASPI_CLKSPEED:-12000000} qcaspi_burst_len=${QCASPI_BURST:-5000} qcaspi_pluggable=${QCASPI_PLUGGABLE:-1} || {
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
QCA_IF=""
for i in {1..30}; do
  # Prefer a netdev driven by qcaspi
  for n in /sys/class/net/*; do
    dev=$(basename "$n")
    if ethtool -i "$dev" 2>/dev/null | grep -qi '^driver:\s*qcaspi'; then
      QCA_IF="$dev"; break
    fi
  done
  [ -n "$QCA_IF" ] && break
  sleep 0.2
done
if [ -z "$QCA_IF" ]; then
  # Fallback guesses
  if ip link show plc0 >/dev/null 2>&1; then QCA_IF=plc0; elif ip link show eth1 >/dev/null 2>&1; then QCA_IF=eth1; fi
fi
echo "[plc-soft-reset] Detected PLC if: ${QCA_IF:-<none>}"

echo "[plc-soft-reset] Bringing iface up and permissive ..."
if [ -n "$QCA_IF" ]; then
  ip link set "$QCA_IF" up 2>/dev/null || true
  ip link set "$QCA_IF" promisc on multicast on allmulticast on 2>/dev/null || true
fi
# Optionally set a static MAC to keep PLC L2 identity stable across resets
if [ -n "$QCA_IF" ] && [ -n "${EVSE_PLC_STATIC_MAC:-}" ]; then
  ip link set dev "$QCA_IF" address "${EVSE_PLC_STATIC_MAC}" 2>/dev/null || true
fi

echo "[plc-soft-reset] ethtool driver stats:"
[ -n "$QCA_IF" ] && ethtool -S "$QCA_IF" || true
echo "[plc-soft-reset] Done."
