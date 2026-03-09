#!/usr/bin/env bash
# Quick SSH connector to Pi with diagnostics

PI_IP="192.168.50.2"
PI_USER="pi04b"
PI_PASS="123456"

echo "Connecting to Pi at $PI_IP..."

# Use sshpass if available, otherwise try manual connection
if command -v sshpass &> /dev/null; then
    echo "Using sshpass..."
    sshpass -p "$PI_PASS" ssh -o StrictHostKeyChecking=no "$PI_USER@$PI_IP" << 'REMOTE_CMD'
echo "=== Pi Diagnostic ==="
echo ""
echo "[1] Network interfaces"
ip -brief addr show | grep -E "eth|wlan"
echo ""
echo "[2] UDP 5000 listener status"
netstat -tuln | grep 5000 || echo "Not listening"
echo ""
echo "[3] Process check"
ps aux | grep -E "pi_rover|python3" | grep -v grep || echo "Not running"
echo ""
echo "[4] UART device"
ls -la /dev/serial0 2>/dev/null || echo "Not found"
echo ""
echo "[5] Running diagnostic..."
python3 /home/pi04b/Documents/sys/diagnose_rc_link.py 2>&1 || echo "Script not found or failed"
REMOTE_CMD
else
    echo "sshpass not found. Please install: sudo apt install sshpass"
    echo "Attempting direct SSH (will prompt for password)..."
    ssh "$PI_USER@$PI_IP" 'ps aux | grep -E "pi_rover|python3" | grep -v grep'
fi
