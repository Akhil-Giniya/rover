#!/usr/bin/env bash
# Step-by-step commands to run ON THE RASPBERRY PI directly (via SSH or direct console)

echo "=== Run these commands ON THE RASPBERRY PI ==="
echo ""
echo "First, SSH into the Pi or connect directly and run:"
echo "  ssh pi04b@192.168.50.2"
echo ""
echo "Then copy & paste these commands one by one:"
echo ""

cat << 'COMMANDS'
# 1. Check if process is running
echo "[1] Is pi_rover_system running?"
pgrep -af pi_rover_system && echo "✓ YES" || echo "✗ NO"

# 2. Check UDP listening
echo ""
echo "[2] Is UDP 5000 listening?"
netstat -tuln | grep ":5000" && echo "✓ YES" || echo "✗ NO"

# 3. Listen for incoming RC packets (has timeout)
echo ""
echo "[3] Listening for RC packets on UDP 5000 for 5 seconds..."
timeout 5 nc -ul 0.0.0.0 5000 || echo "Timeout (no packets received)"

# 4. Check UART
echo ""
echo "[4] UART device status"
ls -la /dev/serial0

# 5. Check dashboard is running
echo ""
echo "[5] Is Flask/dashboard running?"
netstat -tuln | grep ":8080" && echo "✓ YES" || echo "✗ NO"

# 6. Check system logs (if using systemd)
echo ""
echo "[6] Recent system logs"
journalctl -u pi_rover_system -n 20 --no-pager 2>/dev/null || echo "(systemd service not found)"

# 7. Manual test - from another terminal on Pi, send test data
echo ""
echo "[7] MANUAL TEST - Run this in ANOTHER terminal while above listens:"
echo "     echo '1500 1500 1000 2000 1500 1500 1500 1500 1500 1500 1500 1500 1500 1500' | nc -w 1 127.0.0.1 5000"
COMMANDS

echo ""
echo "=== Interpretation Guide ==="
echo ""
echo "If pi_rover_system is NOT running:"
echo "  → Start it: cd /home/pi04b/Documents/sys && python3 pi_rover_system.py"
echo ""
echo "If UDP 5000 is NOT listening:"
echo "  → This systemically means either:"
echo "    a) pi_rover_system crashed on startup"
echo "    b) Port is already in use by something else"
echo ""
echo "If listening but receiving NO packets from PC:"
echo "  → Check PC RC sender is sending to CORRECT Pi IP:"
echo "    python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 --pi-ip 192.168.50.2 --pi-port 5000"
echo ""
