# Underwater Rover Control System - Complete Setup Guide

Professional-grade Raspberry Pi native control system for underwater rovers. Features Ethernet-only communication (no Wi-Fi), real-time dashboard telemetry/logging, and robust failsafe timeout protection.

## Documentation Layout

- Core docs are in the repository root: `README.md`, `QUICK_REFERENCE.md`, `TROUBLESHOOTING.md`
- Archived/legacy notes are in `docs/archive/`
- Documentation index is in `docs/README.md`

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Remote Operator (Laptop/PC)                                 │
│                                                              │
│  Flysky Remote (2.4GHz) ──→ CP2102 USB Serial               │
│                                                              │
│  /dev/ttyUSB0 ──[pc_rc_sender.py]→ UDP:5000 ─────────┐     │
└──────────────────────────────────────────────────────┼──────┘
                                                       │
                         Ethernet Cable (RJ45)         │
                                                       ↓
┌──────────────────────────────────────────────────────────────┐
│ Raspberry Pi 4B (<PI_IP>)                              │
│                                                              │
│  UDP:5000 ─[pi_rover_system.py]→                            │
│    • Bridge Loop (receive RC, forward to ESP32)              │
│    • Flask Dashboard (0.0.0.0:8080)                          │
│                                                              │
│  /dev/serial0 (GPIO14 TX / GPIO15 RX) ──[UART 115200]→ ESP32│
│                                                              │
│  ← UART Response (logs, status) ←                           │
│                                                              │
│  http://<PI_IP>:8080 ← Access Web Dashboard           │
└──────────────────────────────────────────────────────────────┘
                                    ↓
                         GPIO14 (TX) / GPIO15 (RX)
                                    ↓
┌──────────────────────────────────────────────────────────────┐
│ ESP32 Microcontroller (Underwater Unit)                     │
│                                                              │
│  Serial2 (RX GPIO16 / TX GPIO17) ──→ Parse RC Commands     │
│                                                              │
│  Thruster Control (ESC PWM) ──→ Motors                      │
│  Servo Control (SG90) ──→ Aux Servo Outputs                 │
│  Light Control (Relay) ──→ Lights                           │
│                                                              │
│  Status Messages ──→ Back to Pi (logged on dashboard)        │
└──────────────────────────────────────────────────────────────┘
```

## Component Bill of Materials (BOM)

| Component | Quantity | SKU/Model | Notes |
|-----------|----------|-----------|-------|
| Raspberry Pi 4B | 1 | 2GB+ RAM | $35-55 |
| ESP32-DevKit | 1 | Popular variant | $5-10 |
| CP2102 USB Serial | 1 | TTL 3.3V | $2-5 |
| Flysky FS-i6 Remote | 1 | TX + RX module | $30-50 |
| USB-Micro Cable | 1 | For CP2102 | $2 |
| Ethernet Cable | 1 | RJ45 Cat5e/6 | $3 |
| 3.3V UART Level Shifter | 1 | Optional if ESP32 5V intolerant | $1 |
| Motor ESCs BLHeli | 2+ | 30A underwater rated | $20-40 |
| Brushless Motors | 2+ | T200 or similar thruster | $40-80 |
| Li-Po Battery | 1 | 3S (11.1V), rated for voltage | $20-50 |
| **Total System** | — | — | **~$200-300** |

## Quick Start (5 Minutes)

### 1. Copy Files to Raspberry Pi

```bash
# On your laptop, assuming files are in ~/sys/
scp -r ~/sys/* pi@<PI_IP>:/home/pi/sys/
```

### 2. Enable UART on Pi (One-time Setup)

```bash
ssh pi@<PI_IP>
sudo raspi-config
# → Interface Options → Serial Port
# → "Would you like a login shell accessible over serial?" → NO
# → "Would you like the serial port hardware accessible?" → YES
# → Reboot
```

### 3. Install Python Dependencies

```bash
ssh pi@<PI_IP>
cd ~/sys
python3 -m pip install -r requirements.txt
```

### 4. Start Pi Service

```bash
ssh pi@<PI_IP>
cd ~/sys
python3 pi_rover_system.py \
  --listen-ip 0.0.0.0 --listen-port 5000 \
  --uart-port /dev/serial0 --baud 115200
```

Expected output:
```
============================================================
UNDERWATER ROVER SYSTEM - STARTING
============================================================

📡 NETWORK CONFIGURATION:
   UDP Listen:   0.0.0.0:5000
   Web Dashboard: 0.0.0.0:8080
   → IP Address: <PI_IP>
   → Access Dashboard: http://<PI_IP>:8080

✓ Services started. Dashboard running on port 8080
```

### 5. Open Dashboard (from Laptop)

In your browser: **http://<PI_IP>:8080**

Expected to see:
- "RC link: LOST" (yellow/red) until PC sender starts
- Empty logs

### 6. Flash ESP32 Firmware

#### Required Arduino Libraries
Before uploading, install these libraries via **Tools → Manage Libraries** in Arduino IDE:
1. `IBusBM` (by Brian Taylor)
2. `MPU9250_WE` (by Wolfgang Ewald)
3. `MS5837` (by Blue Robotics)
4. `ESP32Servo` (by Kevin Harrington)
5. `Adafruit_NeoPixel` (by Adafruit)

#### Steps:
```bash
# Download Arduino IDE: https://www.arduino.cc/en/software
# Install ESP32 board support: Tools → Board Manager → "esp32" by Espressif

# Open esp32_receiver.ino in Arduino IDE
# Tools → Board → "ESP32 Dev Module"
# Tools → Port → "/dev/ttyUSB0" (your ESP32 COM port)
# Sketch → Upload (Ctrl+U)

# Expected serial output (115200 baud):
# BOOT: setup start
# BOOT: iBus init done
# BOOT: IMU init OK ...
```

### 7. Start PC RC Sender (on Laptop)

```bash
# Linux/Mac
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip <PI_IP> \
  --pi-port 5000

# Windows (COM3 example)
python3 pc_rc_sender.py ^
  --serial-port COM3 ^
  --pi-ip <PI_IP> ^
  --pi-port 5000
```

Expected output:
```
Reading iBUS on /dev/ttyUSB0 @ 115200
Sending UDP to <PI_IP>:5000 at ~50.0 Hz
Move Flysky sticks to start sending data...

TX: 1500 1500 1000 2000 1500 1500 1500 1500 1500 1500 1500 1500 1500 1500
TX: 1500 1500 1000 2000 1500 1500 1500 1500 1500 1500 1500 1500 1500 1500
```

**Dashboard should now show:**
- ✅ "RC link: LIVE" (green)
- ✅ "Last RC age: 0.02s"
- ✅ Real-time packet counts
- ✅ Logs: "[HH:MM:SS] RC: 1500 1500 1000..."

## Hardware Setup (Detailed)

### Raspberry Pi GPIO Pinout (UART to ESP32)

```
Pi Top View (GPIO Header shown)
                ┌─────────────┐
                │ Pi GPIO 1   │
                │ (5V, GND)   │
    ┌─────────────────────────────────┐
    │  3V3      GND     TX(14) RX(15) │  ← Serial0 Header
    │   •        •         •      •   │
    │   1   2   3   4   5   6   7   8 │  Pin numbers (looking down)
    └─────────────────────────────────┘
```

**UART Wiring (Pi → ESP32):**

| Pin | RPi Signal | Phys Pin# | ESP32 Target | Notes |
|-----|-----------|-----------|--------------|-------|
| TX | GPIO14 | 8 | RX (GPIO16) | 3.3V signal |
| RX | GPIO15 | 10 | TX (GPIO17) | 3.3V signal |
| GND | GND | 6,9,14,20,25,30,34,39 | GND | Common ground |

**Recommended Connection Method:**

1. Use Dupont/3-pin female connectors on GPIO header
2. Solder wires to ESP32 RX(GPIO16), TX(GPIO17), GND
3. Use 100mm wires for underwater potting/conformal coating
4. Add 470Ω series resistors on TX/RX lines for protection

### Ethernet Connection (Pi ↔ Laptop)

- Plug RJ45 Ethernet directly between Pi and laptop Ethernet port
- Or use network switch if multiple devices
- Pi will be reachable at `<PI_IP>` (with default eth0 IP config)

### USB Serial (Laptop ↔ CP2102 ↔ Flysky)

**CP2102 Pinout:**
```
    +5V USB
     │
    GND─────────┐
     │          │
    [CP2102]    │
     │          │
    TX(0) ──→ Flysky RX (yellow wire)
    RX(1) ←── Flysky TX (green wire)
     │
    GND ────── Flysky GND (black wire)
```

- Flysky FS-i6 includes RX module (sensitivity controlled)
- CP2102 provides USB-TTL conversion at 115200 baud
- No level shifting needed (Flysky RX is 3.3V tolerant)

## Configuration & Customization

### Change Dashboard Port

```bash
python3 pi_rover_system.py --web-port 9000
# Access at http://<PI_IP>:9000
```

### Change UDP Listen Port

```bash
python3 pi_rover_system.py --listen-port 12345
# Update PC sender: --pi-port 12345
```

### Increase Failsafe Timeout

```bash
python3 pi_rover_system.py --failsafe-timeout 2.0
# Now waits 2 seconds before NO_SIGNAL (default: 1.0)
```

### Disable Ethernet-only (Enable Wi-Fi Debug)

```bash
# Comment out lines in ethernet_only_setup.sh and run:
python3 pi_rover_system.py --eth-interface wlan0
# Then access dashboard over Wi-Fi IP
```

## Troubleshooting

### Dashboard shows "RC link: LOST"

**Symptoms:** Dashboard accessible, but RC link status is red/yellow

**Diagnosis steps:**

1. **Check PC sender is running:**
   ```bash
   # Run on laptop
   python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 \
     --pi-ip <PI_IP> --pi-port 5000
   # Should print "TX: 1500 1500..." every 10th packet
   ```

2. **Verify Pi is listening on UDP 5000:**
   ```bash
   ssh pi@<PI_IP>
   sudo netstat -ulnp | grep 5000
   # Should show: udp  0  0 0.0.0.0:5000  0.0.0.0:*  <PID>/python3
   ```

3. **Test network connectivity:**
   ```bash
   # From laptop to Pi
   ping <PI_IP>
   # Should respond: 64 bytes from <PI_IP>: ...
   ```

4. **Check firewall (if applicable):**
   ```bash
   # macOS/Linux
   sudo lsof -i :5000
   # Should show pi_rover_system.py listening
   ```

5. **Send test packet manually:**
   ```bash
   # On laptop
   python3 -c "
   import socket
   s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
   s.sendto(b'1500 1500 1000 2000 1500 1500 1500 1500 1500 1500 1500 1500 1500 1500', 
            ('<PI_IP>', 5000))
   "
   
   # Dashboard should briefly show "RC link: LIVE"
   ```

### Dashboard shows "UART RX lines: 0" (ESP32 not responding)

**Symptoms:** RC link shows LIVE, packets being received, but ESP32 responses never appear

**Diagnosis steps:**

1. **Verify UART wiring physically:**
   ```bash
   ssh pi@<PI_IP>
   
   # Test UART device exists and is writable
   python3 -c "
   import serial
   ser = serial.Serial('/dev/serial0', 115200, timeout=1)
   ser.write(b'TEST\n')
   response = ser.readline()
   print(f'Response: {response}')
   ser.close()
   "
   ```

2. **Check ESP32 is flashed and powered:**
   - Connect ESP32 USB to laptop
   - Open Arduino IDE → Tools → Serial Monitor
   - Should see "ESP32 UART receiver ready\n"
   - Move Flysky sticks → Should see "RC: 1500 1500..." printed

3. **Verify UART baud rate match:**
   ```bash
   # Both Pi and ESP32 must use 115200
   # Pi: pi_rover_system.py --baud 115200 (default)
   # ESP32: Serial2.begin(115200, SERIAL_8N1, 16, 17);
   ```

4. **Check GPIO pin mapping (if using custom setup):**
   ```bash
   # Default:
   # Pi GPIO14 (UART TX) → ESP32 GPIO16 (RX)
   # Pi GPIO15 (UART RX) → ESP32 GPIO17 (TX)
   
   # Test with loopback (Pi only):
   python3 -c "
   import serial
   ser = serial.Serial('/dev/serial0', 115200, timeout=1)
   ser.write(b'HELLO\n')  # Send
   response = ser.readline()  # Receive (will be empty if no ESP32)
   print(f'Echo: {response}')
   "
   ```

5. **Add debugging via serial monitor:**
   ```cpp
   // Add to esp32_receiver.ino handlePacket():
   void handlePacket(const String &packet) {
     Serial.println("DEBUG: Received packet");
     Serial.print("DEBUG: Length = ");
     Serial.println(packet.length());
     Serial.print("DEBUG: Content = ");
     Serial.println(packet);
     // ... rest of function
   }
   ```

### Serial Port Permission Denied: /dev/ttyUSB0

**Symptoms:** `PermissionError: [Errno 13] Could not open port /dev/ttyUSB0`

**Solution (pick one):**

**Option 1: Run with sudo (quick)**
```bash
sudo python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 \
  --pi-ip <PI_IP> --pi-port 5000
```

**Option 2: Add user to dialout group (permanent)**
```bash
# On laptop
sudo usermod -aG dialout $USER
# Logout and login again (or restart terminal)
python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 ...
```

**Option 3: Change device permissions (temporary)**
```bash
sudo chmod 666 /dev/ttyUSB0
python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 ...
# Resets after reboot
```

## Performance Metrics

**Expected performance on Raspberry Pi 4B:**

| Metric | Value | Notes |
|--------|-------|-------|
| UDP Packet Rate | 50 Hz | 20ms interval |
| UART Baud Rate | 115200 | ~11.5 KBps max throughput |
| Dashboard Latency | <100ms | RC to display update |
| Failsafe Timeout | 1.0s (configurable) | Triggers NO_SIGNAL |
| CPU Usage | 15-25% | Both bridge + dashboard threads |
| Memory Usage | ~80-120 MB | Mostly Python + Flask |

## Advanced: Monitoring & Logging

### Access Pi Service Logs (if using systemd)

```bash
sudo journalctl -u rover -f
# Follow logs in real-time
```

### Export Dashboard Logs (to CSV)

```bash
# Add to Flask route (optional enhancement)
@app.get("/api/logs/export")
def export_logs():
    import csv
    from io import StringIO
    
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=['id', 'ts', 'src', 'msg'])
    writer.writeheader()
    writer.writerows(state.get_logs_since(0))
    
    return output.getvalue(), 200, {'Content-Type': 'text/csv'}
```

### Monitor ESP32 from Dashboard

The dashboard automatically displays all UART messages from ESP32 in the logs panel. Add your own status messages:

```cpp
// In esp32_receiver.ino handlePacket():
void handlePacket(const String &packet) {
  // ...
  Serial2.println("ESP32: motors_active");  // Sent back to Pi → dashboard log
}
```

## File Reference

| File | Purpose | Language |
|------|---------|----------|
| `pi_rover_system.py` | Core Pi service (UDP bridge, UART forwarder, Flask web) | Python 3.9+ |
| `pc_rc_sender.py` | Laptop RC encoder (Flysky iBUS → UDP) | Python 3.9+ |
| `esp32_receiver.ino` | ESP32 firmware (UART RX, failsafe, thruster control) | C++ / Arduino |
| `hardware_check.py` | System diagnostics (Ethernet, UART, UDP) | Python 3.9+ |
| `ethernet_only_setup.sh` | Disable Wi-Fi/Bluetooth on Pi | Bash |
| `requirements.txt` | Python package dependencies (Flask, pyserial) | pip |
| `README.md` | This file | Markdown |

## Support & Common Issues

**System won't start: "Address already in use"**
```bash
# Ports 5000 or 8080 in use by another process
sudo lsof -i :5000
sudo kill <PID>
# Then restart pi_rover_system.py
```

**Flysky receiver not responding**
- Check Flysky battery is installed and charged
- Press bind button on receiver (LED should flash)
- Ensure CP2102 drivers installed on laptop
- Try different USB port

**Can't SSH to Pi**
- Check Ethernet cable is connected
- Restart Pi with power button
- Verify IP address: `arp-scan --localnet | grep pi`

## Next Steps

1. ✅ **Complete**: Verify all hardware connections
2. ✅ **Complete**: Launch Pi service and access dashboard
3. ✅ **Complete**: Flash ESP32 firmware
4. ✅ **Complete**: Run RC sender from laptop
5. ⏭️ **TODO**: Implement thruster ESC control in esp32_receiver.ino
6. ⏭️ **TODO**: Calibrate motor speed curves
7. ⏭️ **TODO**: Add more dashboard telemetry widgets
8. ⏭️ **TODO**: Add light control relay support

## License

MIT License - Free to use, modify, and distribute.
