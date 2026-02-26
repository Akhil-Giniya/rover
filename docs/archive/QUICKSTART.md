#!/usr/bin/env bash
# === QUICK START GUIDE ===
# Copy these commands one at a time into your terminals

# Terminal 1: SSH into Raspberry Pi
echo "=== Terminal 1: SSH to Pi ==="
echo "Run this:"
echo "ssh pi04b@192.168.50.2"
echo "# Enter password: 123456"
echo ""

# Terminal 1 on Pi: Test network
echo "=== Terminal 1 on Pi: Test UDP connectivity ==="
echo "python3 /home/pi04b/Documents/sys/test_udp.py --port 5000 --duration 20"
echo ""

# Terminal 2: From PC, start RC sender
echo "=== Terminal 2 on PC: Start RC sender ==="
echo "cd /path/to/your/rc_sender"
echo "python3 pc_rc_sender.py \"  \\"
echo "  --serial-port /dev/ttyUSB0 \"  \\"
echo "  --pi-ip 192.168.50.2 \"  \\"
echo "  --pi-port 5000 \"  \\"
echo "  --hz 50"
echo ""

# Terminal 1 on Pi: Observe UDP packets received
echo "Expected on Pi UDP listener: Packets arriving from PC with channel data"
echo ""

# Terminal 1 on Pi: Stop test, start dashboard
echo "=== Terminal 1 on Pi: Start Full Rover System ==="
echo "cd /home/pi04b/Documents/sys"
echo "python3 pi_rover_system.py \"  \\"
echo "  --listen-ip 0.0.0.0 \"  \\"
echo "  --listen-port 5000 \"  \\"
echo "  --uart-port /dev/serial0 \"  \\"
echo "  --web-host 0.0.0.0 \"  \\"
echo "  --web-port 8080"
echo ""

# Browser on PC
echo "=== PC Web Browser ===" 
echo "Open: http://192.168.50.2:8080"
echo "You should see:"
echo "  - Dashboard summary panel"
echo "  - Status panel on right"
echo "  - Logs scrolling showing RC data and ESP32 messages"
echo ""

echo "=== Troubleshooting ==="
echo "If RC link is not showing:"
echo "1. Check detailed troubleshooting at:"
echo "   cat TROUBLESHOOTING.md"
echo ""
echo "2. Make sure PC sender is printing TX lines (check console output)"
echo ""
echo "3. Verify network with simple test:"
echo "   On Pi: python3 test_udp.py"
echo "   On PC: echo '1500 1500 1000 2000' | nc 192.168.50.2 5000"
