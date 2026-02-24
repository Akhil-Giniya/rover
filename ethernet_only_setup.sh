#!/usr/bin/env bash
set -euo pipefail

CFG="/boot/firmware/config.txt"

echo "[1/4] Disabling Wi-Fi and Bluetooth at runtime"
sudo rfkill block wifi || true
sudo rfkill block bluetooth || true

echo "[2/4] Stopping radio services"
sudo systemctl stop wpa_supplicant.service || true
sudo systemctl disable wpa_supplicant.service || true
sudo systemctl stop bluetooth.service || true
sudo systemctl disable bluetooth.service || true

echo "[3/4] Making disable persistent in ${CFG}"
if ! grep -q "^dtoverlay=disable-wifi" "$CFG"; then
  echo "dtoverlay=disable-wifi" | sudo tee -a "$CFG" >/dev/null
fi
if ! grep -q "^dtoverlay=disable-bt" "$CFG"; then
  echo "dtoverlay=disable-bt" | sudo tee -a "$CFG" >/dev/null
fi

echo "[4/4] Ethernet status"
ip -brief link show eth0 || true
ip -brief addr show eth0 || true

echo "Done. Reboot recommended: sudo reboot"