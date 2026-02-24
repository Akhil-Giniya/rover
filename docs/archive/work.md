
# 🕒 1-Day Execution Workflow

## ⚡ Phase 1 — Verify Inputs

### Step 1: Confirm PC is receiving Flysky data

On PC:

Check that iBUS values are being read from CP2102.

You should see channel values like:

```text
1500 1500 1000 2000 ...
```

If this isn’t stable → STOP → fix this first.

---

### Step 2: Confirm PC is sending data over Ethernet

Team likely already has this.

Test from Pi:

Run:

```bash
nc -l -p 5000
```

Send test from PC:

```bash
echo "TEST" | nc <pi_ip> 5000
```

If Pi receives → network OK ✅

---

# ⚡ Phase 2 — Pi Receive Layer

Now Pi must listen to RC data.

Write a simple listener:

* TCP or UDP (whatever team is using)
* Just print incoming data

Goal:

👉 See live RC stream on Pi terminal

If you don’t see it → don’t move forward.

---

# ⚡ Phase 3 — UART Setup

Connect Pi → ESP32

Use:

| Pi Pin      | ESP32 |
| ----------- | ----- |
| GPIO14 (TX) | RX    |
| GPIO15 (RX) | TX    |
| GND         | GND   |

Enable UART:

```bash
sudo raspi-config
→ Interface Options
→ Serial
→ Disable login shell
→ Enable serial
```

Test UART:

```bash
echo "HELLO" > /dev/serial0
```

ESP32 should receive.

---

# ⚡ Phase 4 — Build Pass-Through Bridge

Core loop on Pi:

```text
Receive RC packet
↓
Immediately send same packet to UART
```

That’s it.

No parsing
No mapping

Just forwarding.

Run at:

👉 ~30–50 Hz

---

# ⚡ Phase 5 — Add Fail Safe 

If NO RC data for 1 sec:

Send:

```text
NO_SIGNAL
```

ESP32 should stop thrusters.

---
