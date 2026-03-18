# RC Link Troubleshooting Guide

## Problem: "RC is connected to laptop but not showing on dashboard"

This guide helps you diagnose where the RC data is getting lost.

---

## Phase 1: Verify Network Connection

### Step 1a: Can PC reach Pi?

On **PC**:
```bash
ping <PI_IP>
# Should see responses
```

If NO → Check Ethernet cable and router settings.

### Step 1b: Test raw UDP connectivity

On **Pi**:
```bash
python3 test_udp.py --host 0.0.0.0 --port 5000 --duration 15
```

On **PC** (different terminal), start RC sender:
```bash
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip 192.168.50.2 \
  --pi-port 5000 \
  --hz 50
```

**Expected on Pi**: You should see `Packet #1 from 192.168.xxx.xxx:xxxxx` with channel data.

**If you see packets**: Network is OK ✓ → Go to Phase 2

**If NO packets**:
- Make sure PC sender is running (no errors)
- Check that `--pi-ip` in sender is `<PI_IP>` (correct)
- Check firewall on Pi: `sudo ufw status` (should be inactive or have port 5000 allowed)

---

## Phase 2: Dashboard Service Status

### Step 2a: Is pi_rover_system running?

On **Pi**:
```bash
ps aux | grep -E "pi_rover|python3" | grep -v grep
```

**If yes**: Look for line like `python3 pi_rover_system.py...`

**If no**: Start it:
```bash
cd /home/pi04b/Documents/sys
python3 pi_rover_system.py \
  --listen-ip 0.0.0.0 \
  --listen-port 5000 \
  --uart-port /dev/serial0 \
  --baud 115200 \
  --web-host 0.0.0.0 \
  --web-port 8080
```

Watch the startup output. You should see:
```
============================================================
UNDERWATER ROVER SYSTEM - STARTING
============================================================

📡 NETWORK CONFIGURATION:
   UDP Listen:   0.0.0.0:5000
   Web Dashboard: 0.0.0.0:8080
   Ethernet IF:  eth0
   → IP Address: <PI_IP>
   → Access Dashboard: http://<PI_IP>:8080

✓ UDP bound to 0.0.0.0:5000
✓ UART device: /dev/serial0
✓ Bridge loop running - awaiting RC data...
✓ Services started. Dashboard running on port 8080
✓ Waiting for RC packets on UDP 5000...
```

If you see errors like `✗ FATAL: Cannot bind UDP` → Port is in use. Kill the existing process:
```bash
pkill -f pi_rover_system
sleep 1
# Then start again
```

### Step 2b: Access Dashboard

From **PC**, open browser:
```
http://<PI_IP>:8080
```

You should see the rover dashboard with:
- **Status section**: Shows "Ethernet: UP", "RC link: ???", etc.
- **Logs section**: Should show startup messages like "Waiting for first RC packet..."

---

## Phase 3: Verify RC Data Flow

### Step 3a: From dashboard logs

Once pi_rover_system is running and dashboard is open:

1. **From PC**, start RC sender again:
```bash
python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 --pi-ip <PI_IP> --pi-port 5000
```

2. **In dashboard**, watch the **Logs** section (bottom right)

**Expected**: After 1-2 seconds, you should see:
```
[HH:MM:SS] RC: 1500 1500 1000 2000 ...
```

**If you see RC packets**:
- ✓ Network is working
- ✓ pi_rover_system received the data
- Check status shows "RC link: LIVE"

**If you see "Waiting for first RC packet..." but never updates**:
- RC didn't reach pi_rover_system even though test_udp.py worked
- Try restarting pi_rover_system:
```bash
pkill -f pi_rover_system
sleep 1
python3 pi_rover_system.py ...
```

---

## Phase 4: Verify PC Sender

### Step 4a: Is iBUS data correct?

On **PC**, run sender with verbose output:
```bash
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --print-every 1 \
  --pi-ip <PI_IP> \
  --pi-port 5000
```

**Expected output** (every packet):
```
TX: 1500 1500 1000 2000 1500 1500 ...
```

**If nothing prints**:
- Flysky remote is not connected or TTL converter not plugged in
- Check CP2102 serial port is correct (try `/dev/ttyUSB1`, etc.)
- Test iBUS reader directly:
```bash
cat /dev/ttyUSB0
# Move Flysky sticks - should see binary data
```

---

## Quick Checklist

- [ ] PC and Pi are on same Ethernet network
- [ ] `ping <PI_IP>` works
- [ ] `test_udp.py` receives packets when PC sender runs
- [ ] `pi_rover_system.py` is running (check `ps aux`)
- [ ] Dashboard accessible at `http://<PI_IP>:8080`
- [ ] Dashboard logs show "RC:" messages when PC sender running
- [ ] Firefox/Chrome shows live update in dashboard

---

## Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `Cannot bind UDP 0.0.0.0:5000` | Port already in use | `pkill -f pi_rover_system` |
| No packets in test_udp.py | PC sender not running or wrong IP | Start sender with correct `--pi-ip` |
| Dashboard shows "RC link: LOST" | No packets for >1 second | Check PC sender is still sending |
| Dashboard not accessible | Flask/web service crashed | Check startup output, restart pi_rover_system |
| UART WARNING | ESP32 connection issue | Check GPIO14/15 (TX/RX) wiring |

---

## Still Stuck?

1. Collect all output and logs
2. Verify network connectivity with `test_udp.py`
3. Run pi_rover_system in foreground to see errors (not background)
4. Check firewall: `sudo ufw status`
5. Check for duplicate processes: `ps aux | grep pi_rover`
