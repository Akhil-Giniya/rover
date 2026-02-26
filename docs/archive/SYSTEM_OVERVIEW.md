# 🚀 Underwater Rover System - COMPLETE SETUP

## Status: ✓ Full End-to-End System Created

Your complete Ethernet-native rover control stack is ready. This includes:
- **PC RC Sender** (reads Flysky iBUS, sends UDP)
- **Raspberry Pi Dashboard** (receives RC, forwards to ESP32, live web UI)
- **ESP32 Receiver** (UART RX, NO_SIGNAL failsafe)
- **Network Diagnostics** (built-in self-checks)

---

## 🎯 Quick Start (5 Minutes)

### On Raspberry Pi

```bash
# 1. SSH into Pi
ssh pi04b@192.168.50.2
# Password: 123456

# 2. Navigate to project
cd /home/pi04b/Documents/sys

# 3. Start the rover system
python3 pi_rover_system.py \
  --listen-ip 0.0.0.0 \
  --listen-port 5000 \
  --uart-port /dev/serial0 \
  --web-host 0.0.0.0 \
  --web-port 8080
```

### On Laptop (PC)

```bash
# In a new terminal
cd /path/to/project

# Start RC sender (pointing to Pi)
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip 192.168.50.2 \
  --pi-port 5000 \
  --hz 50
```

### In Web Browser

Open: **http://192.168.50.2:8080**

You should see:
- **Status panel** with Ethernet/RC link status
- **Live logs** from Pi and ESP32

---

## 🔧 File Structure

```
/home/akhil/Documents/sys/

# Core System (on Pi)
pi_rover_system.py          ← Main service (UDP→UART bridge + web dashboard)
esp32_receiver.ino          ← ESP32 sketch

# PC Components
pc_rc_sender.py             ← Reads iBUS, sends to Pi

# Testing & Diagnostics
test_udp.py                 ← Network connectivity test
hardware_check.py           ← Physical system validation
diagnose_rc_link.py         ← Diagnostic tool
manual_diagnose.sh          ← Manual check commands

# Setup & Config
ethernet_only_setup.sh      ← Disable Wi-Fi/Bluetooth on Pi
requirements.txt            ← Python dependencies

# Documentation
README.md                   ← Full setup guide
FIX_RC_NOT_SHOWING.md      ← Troubleshooting RC link issues
TROUBLESHOOTING.md          ← General troubleshooting
QUICKSTART.md               ← Copy-paste quick commands
```

---

## ❌ If RC Link Shows "LOST" on Dashboard

**Most Common Cause**: Laptop is sending to wrong IP or sender not running

### Quick Fix:

```bash
# 1. On Laptop, verify sender using correct IP
ps aux | grep pc_rc_sender
# Should see: ... --pi-ip 192.168.50.2 ...

# 2. If wrong IP, restart sender
pkill -f pc_rc_sender
python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 --pi-ip 192.168.50.2 --pi-port 5000

# 3. On Pi, verify dashboard sees the network
python3 test_udp.py --port 5000 --duration 20

# 4. In another Laptop terminal, send test
echo "1500 1500 1000 2000" | nc 192.168.50.2 5000
```

**Full troubleshooting**: See [FIX_RC_NOT_SHOWING.md](FIX_RC_NOT_SHOWING.md)

---

## 🔌 Hardware Wiring

### Raspberry Pi ↔ ESP32 (UART)

| Raspberry Pi | ESP32   |
|--------------|---------|
| GPIO14 (TX)  | RX      |
| GPIO15 (RX)  | TX      |
| GND          | GND     |

### PC ↔ Pi (Ethernet)
- Direct cable or Ethernet switch
- Pi IP: `192.168.50.2`
- Port: UDP `5000` (RC data), TCP `8080` (web dashboard)

---

## 📊 Dashboard Features

### Status Panel (Right Side)
- **Ethernet**: Shows if eth0 is UP
- **RC link**: LIVE (receiving packets) or LOST (timeout)
- **Last RC age**: Time since last packet (should be <0.1s)
- **Packets RX/TX**: Count of received/forwarded packets
- **UART RX lines**: ESP32 messages received
- **Failsafe count**: Number of NO_SIGNAL triggered

### Logs Panel (Bottom Right)
- **Real-time messages** from Pi and ESP32
- **RC packets** (shown every 10th packet to reduce noise)
- **Failsafe triggers** (when RC is lost >1s)
- **System events** (startup, errors, etc.)

## 🛠️ Maintenance

### View Pi Logs (if running as service)
```bash
journalctl -u pi_rover_system -n 50 --no-pager
```

### Stop System
```bash
# On Pi
pkill -f pi_rover_system
```

### Restart System
```bash
# On Pi
pkill -f pi_rover_system
sleep 1
python3 pi_rover_system.py ...
```

### Check for Port Conflicts
```bash
# On Pi
netstat -tuln | grep -E "5000|8080"
```

---

## 📦 Dependencies

### On Raspberry Pi
```bash
pip install -r requirements.txt
# Installs: Flask, pyserial
```

### On PC Laptop
```bash
pip install -r requirements.txt
```

---

## 🚨 Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| Dashboard won't load | Flask service crashed | Check `pi_rover_system.py` startup output |
| RC link shows LOST | No UDP packets arriving | Check sender IP is `192.168.50.2` |
| Cannot bind port 5000 | Port already in use | `pkill -f pi_rover_system` |
| UART errors | ESP32 not connected | Check GPIO14/15 wiring |
| Wi-Fi/Bluetooth still on | Not disabled properly | Run `ethernet_only_setup.sh` and reboot |

---

## 🎓 Understanding the Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│ FLYSKY REMOTE (Connected to Laptop)                         │
└──────────────────┬──────────────────────────────────────────┘
                   │ (TTL Serial, 115200 baud)
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ LAPTOP (PC)                                                 │
│ ├─ CP2102 (USB-Serial adapter)                             │
│ ├─ pc_rc_sender.py (parses iBUS, sends UDP)               │
│ └─ Sends: "1500 1500 1000 2000 ..." → 192.168.50.2:5000   │
└──────────────────┬──────────────────────────────────────────┘
                   │ (Ethernet UDP)
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ RASPBERRY PI (192.168.50.2)                                │
│ ├─ pi_rover_system.py (listens UDP 5000)                  │
│ ├─ Forwards data → UART (/dev/serial0)                    │
│ ├─ Reads UART responses (ESP32 logs)                      │
│ ├─ Failsafe: sends NO_SIGNAL if loss >1s                 │
│ └─ Web Dashboard (Flask) on port 8080                     │
└──────────────────┬──────────────────────────────────────────┘
                   │ (UART Serial, 115200 baud)
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ ESP32                                                       │
│ ├─ Listens UART (/dev/Serial2)                            │
│ ├─ On RC packet: logs "RC: 1500 1500..."                  │
│ ├─ On NO_SIGNAL: triggers "ACTION: STOP_THRUSTERS"        │
│ └─ Controls thrusters based on channel values             │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ Verification Checklist

Before declaring system ready:

- [ ] Pi accessible at `192.168.50.2` (ping works)
- [ ] Dashboard loads at `http://192.168.50.2:8080`
- [ ] Status shows "Ethernet: UP"
- [ ] RC sender running with `--pi-ip 192.168.50.2`
- [ ] Status shows "RC link: LIVE" (when sender active)
- [ ] Logs show incoming RC packets
- [ ] Moving Flysky sticks updates Last RC value
- [ ] ESP32 connected via UART and receiving data
- [ ] Failsafe triggers after 1s without RC

All ✓ = **System Ready for Deployment** 🎉

---

## 🎬 Next Steps

1. **Flash ESP32 sketch**: Load `esp32_receiver.ino` onto ESP32
2. **Wire ESP32 to Pi**: GPIO14/15 (TX/RX) connections
3. **Wire thrusters**: Connect ESCs to ESP32 PWM outputs
4. **Run full test**: Start all components and verify end-to-end
5. **Deploy**: Submerge rover and test in water

---

## 📞 Support

For issues, consult:
- **RC Link Not Showing**: [FIX_RC_NOT_SHOWING.md](FIX_RC_NOT_SHOWING.md)
- **General Troubleshooting**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- **Network Diagnostics**: Run `python3 test_udp.py`
- **Hardware Validation**: Run `python3 hardware_check.py`

Good luck! 🤖🌊
