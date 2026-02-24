# 🎯 ACTION PLAN - RC Link Not Showing on Dashboard

## Your Situation
- ✓ RC remote connected to **LAPTOP** via CP2102 TTL converter
- ✓ Laptop & Raspberry Pi connected via **Ethernet** (192.168.50.2)
- ✗ Dashboard shows RC link as "LOST" or not updating
- ✗ Camera feed may not be showing

---

## Immediate Debug Steps (Do These First)

### Step 1: Verify Network is Working

**On Laptop**:
```bash
ping 192.168.50.2
# Should get responses, NOT "host unreachable"
```

**If ping FAILS**:
- Ethernet cable disconnected?
- Pi powered on?
- Wrong IP address?

→ **Fix before continuing**

---

### Step 2: Check if RC Sender is Actually Running

**On Laptop**:
```bash
# Check if process exists
ps aux | grep pc_rc_sender | grep -v grep

# Or, in the terminal where you started it:
# Should be printing lines like: TX: 1500 1500 1000 2000 ...
```

**If NOT running**:
```bash
cd /home/akhil/Documents/sys

# Start it with correct Pi IP
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --piip 192.168.50.2 \
  --pi-port 5000 \
  --hz 50
```

**If it starts but shows errors**: Your iBUS data might be wrong. Check:
```bash
cat /dev/ttyUSB0
# Move Flysky sticks - you should see BINARY data (not text)
```

---

### Step 3: Verify Pi Dashboard is Running

**On Laptop**, open browser:
```
http://192.168.50.2:8080
```

**If page loads**:
- Dashboard is running ✓
- Check the **Status** section (right panel)
- Look for "RC link:" line

**If page doesn't load**:
- SSH into Pi and restart:
```bash
ssh pi04b@192.168.50.2
pkill -f pi_rover_system
python3 pi_rover_system.py --listen-ip 0.0.0.0 --listen-port 5000 --uart-port /dev/serial0 --web-host 0.0.0.0 --web-port 8080
```

---

### Step 4: Direct UDP Test (Network Connectivity)

**On Raspberry Pi**:
```bash
ssh pi04b@192.168.50.2
python3 /home/pi04b/Documents/sys/test_udp.py --port 5000 --duration 20
```

**On Laptop** (in a new terminal), while test is running:
```bash
# Send a test packet
echo "1500 1500 1000 2000" | nc 192.168.50.2 5000
```

**Expected result on Pi**:
```
[HH:MM:SS] Packet #1 from 192.168.xxx.xxx:xxxxx
  → 1500 1500 1000 2000
```

**If NO packets**:
- Firewall blocking port 5000 on Pi? Run: `sudo ufw status`
- Wrong IP in sender? Verify with `ps aux | grep pc_rc_sender`

**If YES packets**:
- Network is working ✓ → go to Step 5

---

### Step 5: Full End-to-End Test

**On Pi**: 
```bash
ssh pi04b@192.168.50.2
# Make sure dashboard is running
pgrep -af pi_rover_system
# If nothing, start it
python3 pi_rover_system.py --listen-ip 0.0.0.0 --listen-port 5000 ...
```

**On Laptop**:
- Make sure RC sender is running
- Check terminal output - should see `TX: 1500 1500 ...` every 0.02 seconds

**In Browser**: 
```
http://192.168.50.2:8080
```
- Refresh the page
- Watch the **Logs** section at the bottom right
- You should see new log lines appearing in real-time

**Expected**:
```
[HH:MM:SS] RC: 1500 1500 1000 2000 1500 ...
[HH:MM:SS] RC: 1499 1502  998 2002 1500 ...
```

**If you see these logs**:
- ✓✓✓ RC Link IS WORKING! 
- Dashboard Status should show "RC link: LIVE"
- Scroll to **"Last RC:"** - should show channel values

---

## If Still Not Showing RC Data

### Advanced Diagnostics

**1. Check what port the sender is actually using**:
```bash
# On Laptop
lsof -i :UDP | grep python
# Or
netstat -tuan | grep python
```

**2. Check what the dashboard is listening on**:
```bash
# On Pi (SSH)
netstat -tuln | grep 5000
# Should show: 0.0.0.0:5000 in LISTEN state
```

**3. Check if there are TWO services fighting for port 5000**:
```bash
# On Pi
ps aux | grep -E "pi_rover|python" | grep -v grep
# Should see only ONE line (one pi_rover process)

# If TWO or more, kill the old one
pkill -f pi_rover_system
sleep 2
# Restart it fresh
```

---

## Definitive Fix (If Everything Fails)

**Complete reset procedure**:

```bash
# 1. On Laptop, kill sender
pkill -f pc_rc_sender
sleep 2

# 2. On Pi, kill dashboard
ssh pi04b@192.168.50.2 'pkill -f pi_rover_system'
sleep 2

# 3. Verify ports are free
ssh pi04b@192.168.50.2 'netstat -tuln | grep -E "5000|8080"'
# Should show nothing

# 4. On Pi, restart dashboard
ssh pi04b@192.168.50.2 << 'EOF'
cd /home/pi04b/Documents/sys
python3 pi_rover_system.py \
  --listen-ip 0.0.0.0 \
  --listen-port 5000 \
  --uart-port /dev/serial0 \
  --baud 115200 \
  --failsafe-timeout 1.0 \
  --eth-interface eth0 \
  --camera-index 0 \
  --web-host 0.0.0.0 \
  --web-port 8080
EOF

# 5. On Laptop, verify network
ping -c 3 192.168.50.2

# 6. Open dashboard in browser
# http://192.168.50.2:8080

# 7. On Laptop, start sender
python3 /home/akhil/Documents/sys/pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip 192.168.50.2 \
  --pi-port 5000 \
  --hz 50 \
  --print-every 1

# 8. Watch dashboard logs - should see RC packets within 2 seconds
```

---

## What Each Component Should Show

| Component | Expected Output |
|-----------|-----------------|
| **PC iBUS Reader** | Binary data when moving sticks |
| **PC RC Sender** | `TX: 1500 1500 1000 2000 ...` every 0.02s |
| **Pi UDP Listener** | Receives packets from sender IP |
| **Pi Dashboard Status** | "Ethernet: UP", "RC link: LIVE", "Last RC age: < 0.5s" |
| **Pi Dashboard Logs** | `[HH:MM:SS] RC: 1500 1500 ...` appearing continuously |
| **ESP32 Console** | `RC: 1500 1500 ...` when connected via UART |

---

## Root Cause Matrix

| Symptom | Most Likely Cause | Quick Fix |
|---------|------------------|-----------|
| Dashboard loads but RC shows LOST | Sender not running or wrong IP | Check `ps aux \| grep pc_rc_sender` |
| test_udp.py receives packets but dashboard doesn't | Dashboard crashed or restarted | Restart: `pkill -f pi_rover_system` ← `python3 pi_rover_system.py` |
| test_udp.py gets NO packets | Firewall or wrong sender IP | Disable UFW or verify `--pi-ip 192.168.50.2` |
| Dashboard page won't load | Flask crashed on startup | Check startup terminal for errors, check port 8080 is free |
| Camera shows error | Camera not connected or not enabled | `ls -la /dev/video0` |

---

## Final Verification

Once RC appears on dashboard:

- [ ] Status shows "RC link: LIVE"
- [ ] Last RC age < 1.0 seconds
- [ ] Logs show incoming `RC: ...` packets
- [ ] Logs show `ESP32:` messages (if ESP32 connected)
- [ ] Move Flysky sticks → see channel values update
- [ ] Stop sender for ~2 sec → see `FAILSAFE: NO_SIGNAL` in logs

**All ✓ = System is working!**

Next: Verify ESP32 receives data over UART...

---

## Need Help?

1. Copy terminal output (all error messages)
2. Note which step above fails
3. Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for that specific error
4. Verify network with: `python3 test_udp.py`
