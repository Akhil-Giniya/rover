#!/usr/bin/env bash
# =============================================================================
# ethernet_only_setup.sh  —  Configure Pi for Ethernet-only operation
#
# What this does:
#   1. Disables Wi-Fi and Bluetooth radios (runtime + persistent)
#   2. Sets a static IP (192.168.50.2) on eth0 for direct PC→Pi link
#   3. Verifies Ethernet is UP
#
# Run on the Pi:  sudo bash ethernet_only_setup.sh
# =============================================================================
set -euo pipefail

STATIC_IP="192.168.50.2"
NETMASK="24"
GATEWAY="192.168.50.1"
ETH_IF="eth0"
CFG="/boot/firmware/config.txt"
DHCPCD_CONF="/etc/dhcpcd.conf"
NM_CONN_FILE="/etc/NetworkManager/system-connections/eth0-static.nmconnection"

echo "============================================="
echo " Ethernet-Only Pi Setup"
echo "============================================="

# ─── STEP 1: Disable Wi-Fi and Bluetooth ─────────────────────────────────────
echo ""
echo "[1/4] Disabling Wi-Fi and Bluetooth"

# Runtime block
sudo rfkill block wifi 2>/dev/null || true
sudo rfkill block bluetooth 2>/dev/null || true

# Stop and disable services
sudo systemctl stop wpa_supplicant.service 2>/dev/null || true
sudo systemctl disable wpa_supplicant.service 2>/dev/null || true
sudo systemctl stop bluetooth.service 2>/dev/null || true
sudo systemctl disable bluetooth.service 2>/dev/null || true

# Persistent disable via device tree overlays
if ! grep -q "^dtoverlay=disable-wifi" "$CFG" 2>/dev/null; then
    echo "dtoverlay=disable-wifi" | sudo tee -a "$CFG" >/dev/null
    echo "  Added dtoverlay=disable-wifi to $CFG"
fi
if ! grep -q "^dtoverlay=disable-bt" "$CFG" 2>/dev/null; then
    echo "dtoverlay=disable-bt" | sudo tee -a "$CFG" >/dev/null
    echo "  Added dtoverlay=disable-bt to $CFG"
fi

echo "  ✓ Wi-Fi and Bluetooth disabled"

# ─── STEP 2: Configure Static IP on eth0 ─────────────────────────────────────
echo ""
echo "[2/4] Configuring static IP ${STATIC_IP}/${NETMASK} on ${ETH_IF}"

# Detect which network manager is in use
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    echo "  Detected: NetworkManager"

    # Create a NetworkManager connection profile
    sudo tee "$NM_CONN_FILE" > /dev/null <<EOF
[connection]
id=eth0-static
type=ethernet
interface-name=${ETH_IF}
autoconnect=true

[ipv4]
method=manual
addresses=${STATIC_IP}/${NETMASK}
gateway=${GATEWAY}
dns=8.8.8.8;8.8.4.4;

[ipv6]
method=disabled
EOF
    sudo chmod 600 "$NM_CONN_FILE"
    sudo nmcli connection reload 2>/dev/null || true
    sudo nmcli connection up eth0-static 2>/dev/null || true
    echo "  ✓ NetworkManager profile created"

elif [ -f "$DHCPCD_CONF" ]; then
    echo "  Detected: dhcpcd"

    # Check if static config already exists
    if ! grep -q "interface ${ETH_IF}" "$DHCPCD_CONF" 2>/dev/null; then
        sudo tee -a "$DHCPCD_CONF" > /dev/null <<EOF

# === Rover Ethernet Static IP ===
interface ${ETH_IF}
static ip_address=${STATIC_IP}/${NETMASK}
static routers=${GATEWAY}
static domain_name_servers=8.8.8.8 8.8.4.4
EOF
        echo "  ✓ Static IP added to dhcpcd.conf"
    else
        echo "  ✓ Static IP already configured in dhcpcd.conf"
    fi

else
    echo "  Fallback: Using ip command directly"
    sudo ip addr flush dev "${ETH_IF}" 2>/dev/null || true
    sudo ip addr add "${STATIC_IP}/${NETMASK}" dev "${ETH_IF}" 2>/dev/null || true
    sudo ip link set "${ETH_IF}" up 2>/dev/null || true
    echo "  ✓ Static IP set (non-persistent — will reset on reboot)"
    echo "  ⚠ Consider installing dhcpcd5 for persistent config"
fi

# ─── STEP 3: Apply IP immediately ────────────────────────────────────────────
echo ""
echo "[3/4] Applying IP configuration now"

# Try to apply immediately regardless of method
sudo ip addr flush dev "${ETH_IF}" 2>/dev/null || true
sudo ip addr add "${STATIC_IP}/${NETMASK}" dev "${ETH_IF}" 2>/dev/null || true
sudo ip link set "${ETH_IF}" up 2>/dev/null || true
echo "  ✓ Applied ${STATIC_IP}/${NETMASK} on ${ETH_IF}"

# ─── STEP 4: Verify ──────────────────────────────────────────────────────────
echo ""
echo "[4/4] Verification"
echo ""
echo "  Ethernet link status:"
ip -brief link show "${ETH_IF}" 2>/dev/null || echo "  (could not check link)"
echo ""
echo "  Ethernet IP address:"
ip -brief addr show "${ETH_IF}" 2>/dev/null || echo "  (could not check addr)"
echo ""

# Check Wi-Fi is really off
echo "  Wi-Fi status:"
if rfkill list wifi 2>/dev/null | grep -q "Soft blocked: yes"; then
    echo "    ✓ Wi-Fi is BLOCKED"
else
    echo "    ⚠ Wi-Fi may still be active (will be disabled after reboot)"
fi
echo ""

echo "============================================="
echo " DONE!"
echo " Pi IP: ${STATIC_IP}"
echo " PC should be: ${GATEWAY}"
echo ""
echo " Reboot recommended: sudo reboot"
echo "============================================="