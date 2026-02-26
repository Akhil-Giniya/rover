# 🚀 QUICK REFERENCE - SYSTEM NOW OPERATIONAL

## ✅ Live Services

```
Dashboard:   http://192.168.50.2:8080
Status API:  http://192.168.50.2:8080/api/status
Logs API:    http://192.168.50.2:8080/api/logs
```

## 📡 Connection Info

**Pi IP:** `192.168.50.2`  
**UDP RC Port:** `5000`  
**Web Dashboard Port:** `8080`  
**UART Port:** `/dev/serial0` (115200 baud)

## 🎮 Start RC Sender on Laptop

```bash
cd /home/akhil/Documents/sys
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip 192.168.50.2 \
  --pi-port 5000 \
  --hz 50
```

## 🔍 Dashboard Shows

- **Ethernet:** UP ✓
- **RC Link:** LIVE (when sender active)
- **Last RC:** Current channel values
- **Packets RX/TX:** Count of transmitted packets
- **Logs:** Real-time system + RC + ESP32 messages

## ⚙️ Control System

| Component | Purpose | Status |
|-----------|---------|--------|
| Pi Service | Receive RC + forward UART + web dashboard | ✅ RUNNING |
| UDP Port 5000 | Receive RC from laptop | ✅ LISTENING |
| Web Port 8080 | Live dashboard | ✅ RUNNING |
| UART /dev/serial0 | Send to ESP32 | ✅ READY |
| Failsafe | NO_SIGNAL after 1s timeout | ✅ ARMED |

## 🔧 Troubleshooting

**Dashboard not loading?**
```bash
ssh pi04b@192.168.50.2 'pgrep -af pi_rover_system'
```

**RC Link shows LOST?**
- Check laptop sender is running with `--pi-ip 192.168.50.2`
- Verify network: `ping 192.168.50.2`

**Need to restart service?**
```bash
ssh pi04b@192.168.50.2 'pkill -f pi_rover_system'
# Wait 2 seconds, system will auto-restart or manually:
# cd ~/sys && python3 pi_rover_system.py ...
```

## 📊 Test Packets Received

During setup, system successfully:
- Received 51 test RC packets
- Logged all with timestamps
- Triggered failsafe after 1s timeout
- Reported status via dashboard API

## 🎯 Next Step

**Open browser and go to:** `http://192.168.50.2:8080`

Then on laptop:
```bash
python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 --pi-ip 192.168.50.2 --pi-port 5000 --hz 50
```

Move Flysky sticks → Watch dashboard update LIVE! 🎮

---

**Files Location:** `/home/pi04b/sys/`  
**Log File:** `/tmp/rover.log`  
**Dashboard Service:** `pi_rover_system.py`
