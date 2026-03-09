#!/usr/bin/env bash
# Full Pi diagnostic and fix script

set -e

echo "=== Underwater Rover - RC Link Diagnostic & Fix ==="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PI_IP="192.168.50.2"
PI_USER="pi04b"
PI_PASS="123456"

echo -e "${YELLOW}[Step 1] Check local side - is PC sender running?${NC}"
if command -v netstat &>/dev/null; then
    if netstat -tuln 2>/dev/null | grep -q "ESTABLISHED.*5000" || netstat -tuln 2>/dev/null | grep -q ":5000"; then
        echo -e "${GREEN}✓${NC} Port 5000 is active locally"
    else
        echo -e "${YELLOW}⚠${NC} Port 5000 may not be sending data"
    fi
fi

echo ""
echo -e "${YELLOW}[Step 2] Check Pi network connectivity${NC}"
if ping -c 1 -W 2 "$PI_IP" &>/dev/null; then
    echo -e "${GREEN}✓${NC} Pi is reachable at $PI_IP"
else
    echo -e "${RED}✗${NC} Cannot ping Pi at $PI_IP"
    exit 1
fi

echo ""
echo -e "${YELLOW}[Step 3] Remote diagnostics on Pi${NC}"

# Create inline Python diagnostic
cat > /tmp/pi_check.py << 'EOF'
import socket
import subprocess
import sys

# Check listening ports
print("[*] Checking UDP 5000 listener...")
result = subprocess.run(["netstat", "-tuln"], capture_output=True, text=True)
if ":5000 " in result.stdout:
    print("✓ UDP 5000 is listening")
else:
    print("✗ UDP 5000 NOT listening - service may not be running")

# Try to receive packet
print("\n[*] Testing UDP receive on 0.0.0.0:5000...")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind(("0.0.0.0", 5000))
    sock.settimeout(1.0)
    try:
        data, addr = sock.recvfrom(2048)
        print(f"✓ Received packet from {addr}")
    except socket.timeout:
        print("✗ No UDP packets received (timeout)")
except OSError as e:
    print(f"✗ Cannot bind UDP 5000: {e}")
finally:
    sock.close()

# Check process
print("\n[*] Checking process status...")
result = subprocess.run(["pgrep", "-af", "pi_rover_system"], capture_output=True, text=True)
if result.returncode == 0:
    print("✓ pi_rover_system is running")
    print(f"  {result.stdout.strip()}")
else:
    print("✗ pi_rover_system NOT running")
    print("  Start with: python3 pi_rover_system.py --listen-ip 0.0.0.0 --listen-port 5000 ...")

# Check firewall (ufw)
print("\n[*] Checking firewall...")
result = subprocess.run(["sudo", "ufw", "status"], capture_output=True, text=True)
if "inactive" in result.stdout:
    print("✓ UFW firewall is inactive")
elif "5000" in result.stdout:
    print("✓ Port 5000 appears in UFW rules")
else:
    print("⚠ UFW may be filtering port 5000")
    print(f"  {result.stdout}")
EOF

# Run the diagnostic on Pi
echo "Running remote diagnostic..."
sshpass -p "$PI_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$PI_USER@$PI_IP" \
    'cd /tmp && python3 pi_check.py' 2>/dev/null || echo "(Failed to run remote check)"

echo ""
echo -e "${YELLOW}[Step 4] Check if PC is sending to correct address${NC}"
echo "Verify PC sender is using: --pi-ip $PI_IP --pi-port 5000"
echo ""

echo -e "${YELLOW}[Step 5] Manual verification - listen on Pi and send from PC${NC}"
echo ""
echo "On Pi, run:"
echo "  nc -ul 0.0.0.0 5000"
echo ""
echo "From PC, send test:"
echo "  echo '1500 1500 1000 2000' | nc $PI_IP 5000"
echo ""
echo "If data appears on Pi listener, network is OK. If dashboard still doesn't show it,"
echo "the pi_rover_system process may not be running correctly."
echo ""

echo -e "${GREEN}Diagnostic complete${NC}"
