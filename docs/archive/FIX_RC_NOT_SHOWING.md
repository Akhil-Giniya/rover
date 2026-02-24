# RC Link Debugging - Specific to Your Setup

## Your Situation
- **RC Remote**: Connected to LAPTOP (not Pi)
- **Laptop**: Sends RC data to Pi over Ethernet
- **Problem**: Dashboard doesn't show RC link status

---

## Root Cause Analysis

The RC data is connected to your **laptop**, not directly to the Pi. So the flow is:

```
Flysky Remote
    ↓ (TTL/serial)
Laptop CP2102
    ↓ (iBUS parsing)
Laptop RC Sender
    ↓ (UDP/Ethernet)
Pi Port 5000
    ↓ (dashboard receives)
Dashboard shows RC link
```

If dashboard shows RC link as "LOST", it means **UDP packets are not reaching the Pi**.

---

## Diagnostic Flowchart

### Problem: RC Shows "LOST" on Dashboard

**Check 1: Is PC sender running and sending UDP?**
```bash
# On Laptop, in a new terminal while sender is running:
netstat -tuan | grep 5000
```
Look for line like: `UDP ... 192.168.50.* ...`

If **no output**: Sender may not be active. Check sender terminal for errors.

---

**Check 2: Can Pi receive ANY UDP on port 5000?**
```bash
# On Pi, stop the dashboard first
pkill -f pi_rover_system
sleep 1

# Start test listener
python3 test_udp.py --port 5000 --duration 20

# In another terminal on Laptop:
echo "1500 1500 1000 2000" | nc 192.168.50.2 5000
```

- If **test_udp shows packet**: Network is OK ✓
- If **NO packet**: Firewall or IP address issue

---

**Check 3: Is dashboard actually running?**
```bash
# On Pi
pgrep -af pi_rover_system
ps aux | grep 5000 | grep LISTEN
```

Expected: One line showing `python3 pi_rover_system.py`

If **NO process running**: Restart it:
```bash
cd /home/pi04b/Documents/sys
python3 pi_rover_system.py --listen-ip 0.0.0.0 --listen-port 5000 --etc...
```

---

**Check 4: Dashboard webserver running?**
```bash
# On Pi
netstat -tuln | grep 8080
```

Expected: Line showing `LISTEN` on port 8080

If **NO**: Dashboard crashed. Check startup output for errors.

---

## Most Common Issue: Wrong Sender IP

On **Laptop**, verify your RC sender is using the CORRECT Pi IP:

```bash
ps aux | grep pc_rc_sender
```

Look for the actual command line. Make sure it has:
```
--pi-ip 192.168.50.2
```

NOT something else like `192.168.1.x` or `192.168.x.x`

If wrong IP, **kill sender and restart** with correct IP:
```bash
pkill -f pc_rc_sender
python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 --pi-ip 192.168.50.2 --pi-port 5000 --hz 50
```

---

## Step-by-Step Fix (if RC still not showing)

### Step 1: Stop everything
```bash
# On Pi
pkill -f pi_rover_system
sleep 2
```

### Step 2: Start dashboard with verbose output
```bash
# On Pi, in foreground (don't send to background)
cd /home/pi04b/Documents/sys
python3 pi_rover_system.py \
  --listen-ip 0.0.0.0 \
  --listen-port 5000 \
  --uart-port /dev/serial0 \
  --baud 115200 \
  --web-host 0.0.0.0 \
  --web-port 8080
```

**Watch the output**. You should see:
```
✓ UDP bound to 0.0.0.0:5000
✓ Bridge loop running - awaiting RC data...
✓ Services started. Dashboard running on port 8080
✓ Waiting for RC packets on UDP 5000...
```

### Step 3: Start RC sender on Laptop
```bash
# New terminal on Laptop
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip 192.168.50.2 \
  --pi-port 5000 \
  --hz 50 \
  --print-every 1
```

**Watch output**. You should see:
```
TX: 1500 1500 1000 2000 ...
TX: 1500 1498  999 2001 ...
```

### Step 4: Check Pi dashboard output
Back in the **Pi terminal**, watch for:
```
✓ First RC packet received from 192.168.xxx
```

### Step 5: Open dashboard
Browser: `http://192.168.50.2:8080`

Status should now show:
- **RC link:** LIVE ✓
- **Last RC:** 1500 1500 1000 2000 ...
- **Logs:** Scrolling RC packets

---

## If Still Stuck

**Collect This Info**:

1. PC sender output (full terminal dump):
```bash
# On Laptop, run and capture
python3 pc_rc_sender.py ... 2>&1 | head -50
```

2. Pi dashboard output (full terminal dump):
```bash
# On Pi, run in foreground and capture startup
python3 pi_rover_system.py ... 2>&1 | head -50
```

3. Network diagnostics:
```bash
# On Laptop
ping 192.168.50.2
netstat -tuan | grep 5000

# On Pi
ifconfig eth0
netstat -tuln | grep 5000
netstat -tuln | grep 8080
```

---

## Network Verification Commands

**Verify Ethernet is connected:**
```bash
# On Pi
ip link show eth0
# Should show "UP" not "DOWN"

# Check IP
ip addr show eth0
# Should show 192.168.50.2 or similar
```

**Test simple UDP send from Laptop:**
```bash
# On Laptop, send directly to Pi
echo '1500 1500 1000 2000' | nc 192.168.50.2 5000

# On Pi, verify with listener
nc -ul 0.0.0.0 5000
# Press Ctrl+C after test
```

**If NC sends but nothing appears on Pi**: Firewall issue
```bash
# On Pi
sudo ufw status
# Should show "inactive" or have "5000 ALLOW"
```

---

## Final Checklist Before Declaring "Fixed"

- [ ] PC sender `--pi-ip` is `192.168.50.2`
- [ ] Laptop can ping `192.168.50.2`
- [ ] `test_udp.py` receives packets from PC sender
- [ ] Dashboard **starts without errors**
- [ ] Dashboard **shows RC link: LIVE**
- [ ] Dashboard **logs show incoming RC packets**
- [ ] Moving Flysky sticks **updates channel values in dashboard**
- [ ] Stopping sender for ~1 sec **shows FAILSAFE log**

All ✓ = System is working correctly!
