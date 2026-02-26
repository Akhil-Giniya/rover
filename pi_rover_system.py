#!/usr/bin/env python3
"""
Underwater Rover Native Pi Service - Transparent iBUS Bridge + GPIO Control + Telemetry

This module implements the core rover relay system that runs on Raspberry Pi 4B:

ARCHITECTURE:
  • Bridge Loop (UDP Receiver ↔ UART):
    - Binds UDP socket on 0.0.0.0:5000 (listens for raw iBUS frames from PC)
    - Forwards exact 32-byte binary iBUS frames directly to /dev/serial0 (ESP32)
    - Reads UART output from ESP32 and streams it to the web dashboard logs
    
  • Camera Stream:
    - Spawns rpicam-vid process to capture MJPEG video
    - Parses JPEG frame boundaries (0xFFD8 start, 0xFFD9 end)
    - Serves latest JPEG frame to web dashboard every ~33ms
    
  • GPIO Control:
    - Servos on GPIO 12 & 13 (PWM)
    - Combined Relay on GPIO 26:
      - Can be held HIGH (Momentary override)
      - Can be set to BLINK (Toggle mode)
      - Priority: Momentary HIGH > Blink > Off

  • Telemetry & Logging:
    - Monitors Pi system stats (CPU Temp, Voltage, RAM, Network)
    - Aggregates logs from Pi system and ESP32 UART
    - Provides a rich debug console on the dashboard
    
DATA FLOW:
  Flysky RC → CP2102 (USB) → Laptop UDP → Pi UDP:5000 → Pi /dev/serial0 UART → ESP32 RX
"""

import argparse
import struct
import collections
import time
import socket
import select
import subprocess
import threading
import serial
import re
import shutil
from dataclasses import dataclass, field
from typing import Deque, List, Optional

from flask import Flask, Response, jsonify, render_template_string, request

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("Warning: RPi.GPIO not found or not on Pi. GPIO features will be simulated.")

# ─── GPIO CONFIGURATION ─────────────────────────────────────────────────────
PIN_SERVO_1 = 12  # Hardware PWM 0
PIN_SERVO_2 = 13  # Hardware PWM 1
PIN_RELAY_MOMENTARY = 26 # Used for both Momentary and Blink functions

DASHBOARD_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Rover Command Center</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');

    :root {
      --bg-dark: #09090b;
      --card-bg: rgba(23, 23, 28, 0.7);
      --accent: #6366f1;
      --accent-glow: rgba(99, 102, 241, 0.4);
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --text-main: #f3f4f6;
      --text-muted: #9ca3af;
      --border: rgba(255, 255, 255, 0.1);
    }

    body {
      font-family: 'Inter', sans-serif;
      background: radial-gradient(circle at top right, #1e1b4b, #09090b);
      color: var(--text-main);
      margin: 0;
      height: 100vh;
      overflow: hidden;
    }

    .wrap {
      display: grid;
      grid-template-columns: 1fr 380px;
      gap: 20px;
      padding: 20px;
      height: 100vh;
      box-sizing: border-box;
    }

    .card {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 4px 24px rgba(0, 0, 0, 0.3);
      display: flex;
      flex-direction: column;
    }

    h1 { margin: 0 0 16px 0; font-size: 18px; font-weight: 600; letter-spacing: 0.5px; color: var(--text-main); }
    h2 {
      margin: 0 0 16px 0;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--text-muted);
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    /* Live Feed */
    .feed-container {
      position: relative;
      flex-grow: 1;
      border-radius: 12px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: #000;
    }
    img.feed { width: 100%; height: 100%; object-fit: contain; display: block; }

    .status-badge {
      padding: 4px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      background: rgba(255,255,255,0.1);
    }
    .status-badge.live { background: rgba(16, 185, 129, 0.2); color: var(--success); }
    .status-badge.lost { background: rgba(239, 68, 68, 0.2); color: var(--danger); }

    /* Controls */
    .control-group { margin-bottom: 24px; }
    .control-label {
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 8px;
      display: flex;
      justify-content: space-between;
    }
    .slider-container { display: flex; align-items: center; gap: 12px; }

    input[type=range] {
      -webkit-appearance: none; width: 100%; background: transparent; cursor: pointer;
    }
    input[type=range]::-webkit-slider-runnable-track {
      width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px;
    }
    input[type=range]::-webkit-slider-thumb {
      height: 18px; width: 18px; border-radius: 50%; background: var(--accent);
      margin-top: -6px; -webkit-appearance: none; box-shadow: 0 0 10px var(--accent-glow);
      transition: transform 0.1s;
    }
    input[type=range]::-webkit-slider-thumb:hover { transform: scale(1.2); }

    /* Buttons & Toggles */
    .btn-momentary {
      width: 100%;
      background: linear-gradient(135deg, #3b82f6, #2563eb);
      color: white;
      border: none;
      padding: 12px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
      transition: all 0.2s;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .btn-momentary:active { transform: scale(0.98); opacity: 0.9; }

    .switch-row { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; }
    .switch { position: relative; display: inline-block; width: 44px; height: 24px; }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider-toggle {
      position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
      background-color: rgba(255,255,255,0.1); transition: .4s; border-radius: 24px;
    }
    .slider-toggle:before {
      position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px;
      background-color: white; transition: .4s; border-radius: 50%;
    }
    input:checked + .slider-toggle { background-color: var(--accent); }
    input:checked + .slider-toggle:before { transform: translateX(20px); }

    /* Logs Console */
    .console {
      background: rgba(0,0,0,0.5);
      border: 1px solid var(--border);
      border-radius: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 10px;
      flex-grow: 1;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .log-entry { display: flex; gap: 8px; opacity: 0.9; }
    .ts { color: #52525b; min-width: 60px; }
    .tag { font-weight: bold; border-radius: 4px; padding: 0 4px; min-width: 35px; text-align: center; }

    .tag-ESP32 { background: rgba(59, 130, 246, 0.2); color: #60a5fa; }
    .tag-PI { background: rgba(139, 92, 246, 0.2); color: #a78bfa; }
    .tag-RC { background: rgba(16, 185, 129, 0.2); color: #34d399; }
    .tag-SYS { background: rgba(239, 68, 68, 0.2); color: #f87171; }
    .tag-GPIO { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }

    /* Telemetry Grid */
    .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 20px; }
    .stat-box {
      background: rgba(255,255,255,0.03);
      border-radius: 8px;
      padding: 10px;
      text-align: center;
    }
    .stat-val { font-size: 18px; font-weight: 700; color: var(--text-main); }
    .stat-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; margin-top: 4px; }

    /* Responsive */
    @media (max-width: 900px) {
      .wrap { grid-template-columns: 1fr; height: auto; overflow: auto; }
      .feed-container { height: 300px; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <!-- Left Panel: Camera & Quick Actions -->
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
        <h1>ROVER COMMAND</h1>
        <span id="link-badge" class="status-badge lost">DISCONNECTED</span>
      </div>

      <div class="feed-container">
        <img class="feed" src="/video_feed" alt="Live Feed" onerror="var el=this; setTimeout(function(){el.src='/video_feed?t='+Date.now();},2000)">
        <div style="position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); color:var(--text-muted); font-size:12px; z-index:-1;">NO SIGNAL</div>
      </div>

      <div style="display:flex; gap:12px; margin-top:16px;">
        <div class="stat-box" style="flex:1">
            <div id="stat-rc" class="stat-val">--</div>
            <div class="stat-label">Last Packet</div>
        </div>
        <div class="stat-box" style="flex:1">
            <div id="stat-pps" class="stat-val">0</div>
            <div class="stat-label">Packets/Sec</div>
        </div>
      </div>
    </div>

    <!-- Right Panel: Controls & Logs -->
    <div style="display:flex; flex-direction:column; gap:16px;">

      <!-- Controls Card -->
      <div class="card" style="flex: 0 0 auto;">
        <h2>Hardware Control</h2>

        <!-- Servos -->
        <div class="control-group">
          <div class="control-label"><span>Camera Pan (Servo 1)</span> <span id="val-s1" style="color:var(--accent)">90°</span></div>
          <div class="slider-container">
            <input type="range" min="0" max="180" value="90" oninput="updateServo(1, this.value)">
          </div>
        </div>

        <div class="control-group">
          <div class="control-label"><span>Camera Tilt (Servo 2)</span> <span id="val-s2" style="color:var(--accent)">90°</span></div>
          <div class="slider-container">
            <input type="range" min="0" max="180" value="90" oninput="updateServo(2, this.value)">
          </div>
        </div>

        <!-- Relay -->
        <div style="background:rgba(255,255,255,0.03); padding:12px; border-radius:8px;">
            <div class="control-label">AUXILIARY RELAY (GPIO 26)</div>
            <button class="btn-momentary"
                    onmousedown="momentary(true)"
                    onmouseup="momentary(false)"
                    onmouseleave="momentary(false)">
              HOLD TO ACTIVATE
            </button>
            <div class="switch-row">
                <span style="font-size:12px; color:var(--text-muted)">Auto-Blink Mode</span>
                <label class="switch">
                  <input type="checkbox" id="chk-blink" onchange="toggleBlink(this.checked)">
                  <span class="slider-toggle"></span>
                </label>
            </div>
        </div>
      </div>

      <!-- Logs Card -->
      <div class="card" style="flex:1; min-height:300px;">
        <h2>
            System Logs
            <button onclick="clearLogs()" style="background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:10px;">CLEAR</button>
        </h2>
        <div id="console" class="console"></div>
        <div style="display:flex; gap:8px; margin-top:10px; flex-wrap:wrap;">
            <label class="status-badge" style="cursor:pointer"><input type="checkbox" checked onchange="toggleSrc('ESP32')" id="f-ESP32"> ESP32</label>
            <label class="status-badge" style="cursor:pointer"><input type="checkbox" checked onchange="toggleSrc('PI')" id="f-PI"> PI</label>
            <label class="status-badge" style="cursor:pointer"><input type="checkbox" checked onchange="toggleSrc('RC')" id="f-RC"> RC</label>
            <label class="status-badge" style="cursor:pointer"><input type="checkbox" checked onchange="toggleSrc('SYS')" id="f-SYS"> SYS</label>
        </div>
      </div>

    </div>
  </div>

  <script>
    let lastId = 0;
    const filterState = { ESP32: true, PI: true, RC: true, SYS: true, GPIO: true };
    let lastPktCount = 0;
    let lastTime = Date.now();

    async function refreshStatus() {
      try {
        const r = await fetch('/api/status');
        const s = await r.json();

        // Update Link Badge
        const badge = document.getElementById('link-badge');
        if (s.link_alive) {
            badge.className = 'status-badge live';
            badge.innerText = 'LINK ACTIVE';
        } else {
            badge.className = 'status-badge lost';
            badge.innerText = 'SIGNAL LOST';
        }

        // Update Stats
        document.getElementById('stat-rc').innerText = s.last_rc_age_sec < 900 ? s.last_rc_age_sec.toFixed(2) + 's' : '--';

        // Calculate PPS (approx)
        const now = Date.now();
        if (now - lastTime > 1000) {
            const pps = Math.round((s.packets_rx - lastPktCount) * 1000 / (now - lastTime));
            document.getElementById('stat-pps').innerText = pps > 0 ? pps : 0;
            lastPktCount = s.packets_rx;
            lastTime = now;
        }

      } catch(e) { console.error(e); }
    }

    async function refreshLogs() {
      try {
        const r = await fetch('/api/logs?since=' + lastId);
        const data = await r.json();
        const box = document.getElementById('console');
        const shouldScroll = box.scrollHeight - box.scrollTop === box.clientHeight;

        data.logs.forEach(item => {
            lastId = item.id;
            const row = document.createElement('div');
            row.className = `log-entry row-${item.src}`;

            let color = '#ccc';
            if (item.msg.includes('ERR') || item.msg.includes('FAIL')) color = '#ef4444';
            else if (item.msg.includes('WARN')) color = '#f59e0b';

            row.innerHTML = `
                <span class="ts">${item.ts}</span>
                <span class="tag tag-${item.src}">${item.src}</span>
                <span style="color:${color}">${item.msg}</span>
            `;

            if (!filterState[item.src] && filterState[item.src] !== undefined) {
                row.style.display = 'none';
            }
            box.appendChild(row);
        });

        // Limit history
        while (box.children.length > 200) box.removeChild(box.firstChild);

        if (shouldScroll || data.logs.length > 0) box.scrollTop = box.scrollHeight;
      } catch(e) {}
    }

    function toggleSrc(src) {
        filterState[src] = document.getElementById('f-' + src).checked;
        document.querySelectorAll('.row-' + src).forEach(r => r.style.display = filterState[src] ? 'flex' : 'none');
    }

    function updateServo(id, val) {
        document.getElementById('val-s'+id).innerText = val + '°';
        fetch('/api/servo/'+id, {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({angle: parseInt(val)})
        });
    }

    function momentary(active) {
        fetch('/api/gpio/momentary', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({active: active})
        });
    }

    function toggleBlink(active) {
        fetch('/api/gpio/blink', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({active: active})
        });
    }

    function clearLogs() {
        document.getElementById('console').innerHTML = '';
    }

    setInterval(refreshStatus, 500);
    setInterval(refreshLogs, 500);
    refreshStatus();
    refreshLogs();
  </script>
</body>
</html>
"""

# ── iBUS parsing (same protocol as pc_rc_sender.py) ─────────────────────────
IBUS_FRAME_LEN = 32
IBUS_HEADER = b"\x20\x40"

def parse_ibus_frame(frame: bytes):
    """Parse raw 32-byte iBUS frame → list of 14 channel ints, or None if invalid."""
    if len(frame) != IBUS_FRAME_LEN or frame[:2] != IBUS_HEADER:
        return None
    expected = struct.unpack_from("<H", frame, 30)[0]
    checksum = (0xFFFF - (sum(frame[:30]) & 0xFFFF)) & 0xFFFF
    if checksum != expected:
        return None
    return [struct.unpack_from("<H", frame, 2 + 2 * i)[0] for i in range(14)]

def now_ts() -> str:
    return time.strftime("%H:%M:%S")

def check_ethernet_up(interface: str) -> bool:
    try:
        result = subprocess.run(
            ["ip", "-brief", "link", "show", interface],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        return " UP " in f" {result.stdout.strip()} "
    except Exception:
        return False

# ─── TELEMETRY HELPERS ──────────────────────────────────────────────────────
def get_pi_temp():
    try:
        r = subprocess.check_output(["vcgencmd", "measure_temp"]).decode()
        return r.replace("temp=", "").strip()
    except:
        return "N/A"

def get_pi_volts():
    try:
        r = subprocess.check_output(["vcgencmd", "measure_volts"]).decode()
        return r.replace("volt=", "").strip()
    except:
        return "N/A"

def get_ram_usage():
    try:
        r = subprocess.check_output(["free", "-h"]).decode().splitlines()[1]
        # Parse 'Mem: Total Used Free ...'
        parts = r.split()
        return f"{parts[2]}/{parts[1]}"
    except:
        return "N/A"

def get_throttled_state():
    try:
        r = subprocess.check_output(["vcgencmd", "get_throttled"]).decode()
        val = int(r.split("=")[1], 16)
        if val == 0: return "OK"
        msgs = []
        if val & 0x1: msgs.append("Under-voltage")
        if val & 0x2: msgs.append("Freq-capped")
        if val & 0x4: msgs.append("Throttled")
        if val & 0x8: msgs.append("Soft-temp-limit")
        return ", ".join(msgs)
    except:
        return "Unknown"

@dataclass
class SharedState:
    last_rc_time: float = 0.0
    last_rc_sender: str = ""       
    packets_rx: int = 0
    packets_uart_tx: int = 0
    uart_rx_lines: int = 0
    uart_open: bool = False
    camera_ok: bool = False

    # GPIO State
    blink_active: bool = False
    momentary_active: bool = False
    relay_state: bool = False # Actual output state

    logs: Deque[dict] = field(default_factory=lambda: collections.deque(maxlen=1000))
    next_log_id: int = 1
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_log(self, src: str, msg: str):
        with self.lock:
            self.logs.append({
                "id": self.next_log_id,
                "ts": now_ts(),
                "src": src,
                "msg": msg,
            })
            self.next_log_id += 1

    def get_logs_since(self, since_id: int) -> List[dict]:
        with self.lock:
            return [entry for entry in self.logs if entry["id"] > since_id]

class SystemMonitor:
    def __init__(self, state: SharedState):
        self.state = state
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()

    def _monitor_loop(self):
        while True:
            temp = get_pi_temp()
            volts = get_pi_volts()
            ram = get_ram_usage()
            throttled = get_throttled_state()

            msg = f"Temp: {temp} | Volts: {volts} | RAM: {ram} | Pwr: {throttled}"

            # Log warnings if system is stressed
            if throttled != "OK" and throttled != "Unknown":
                self.state.add_log("SYS", f"⚠ POWER WARNING: {throttled}")

            # Regular info log
            self.state.add_log("SYS", msg)

            time.sleep(10) # Log every 10 seconds

class GpioController:
    def __init__(self, state: SharedState):
        self.state = state
        self.pwm1 = None
        self.pwm2 = None
        self.blink_thread = None
        self.blink_stop_event = threading.Event()

        if GPIO_AVAILABLE:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)

                # Setup Servos
                GPIO.setup(PIN_SERVO_1, GPIO.OUT)
                GPIO.setup(PIN_SERVO_2, GPIO.OUT)
                self.pwm1 = GPIO.PWM(PIN_SERVO_1, 50) # 50Hz
                self.pwm2 = GPIO.PWM(PIN_SERVO_2, 50) # 50Hz
                self.pwm1.start(7.5) # Neutral (90 deg)
                self.pwm2.start(7.5) # Neutral (90 deg)

                # Setup Relay Pin (Shared)
                GPIO.setup(PIN_RELAY_MOMENTARY, GPIO.OUT)
                GPIO.output(PIN_RELAY_MOMENTARY, GPIO.LOW)

                state.add_log("GPIO", "Initialized successfully")
            except Exception as e:
                state.add_log("GPIO", f"Init failed: {e}")
        else:
            state.add_log("GPIO", "Simulated mode (no hardware)")

        # Start background update loop for priority logic
        self.update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self.update_thread.start()

    def set_servo(self, servo_id: int, angle: int):
        # Map 0-180 degrees to 2.5-12.5 duty cycle
        duty = 2.5 + (angle / 18.0)
        duty = max(2.5, min(12.5, duty))

        if self.pwm1 and servo_id == 1:
            self.pwm1.ChangeDutyCycle(duty)
        elif self.pwm2 and servo_id == 2:
            self.pwm2.ChangeDutyCycle(duty)

    def set_momentary(self, active: bool):
        with self.state.lock:
            self.state.momentary_active = active
        # State update handled in loop

    def set_blink(self, active: bool):
        with self.state.lock:
            self.state.blink_active = active
        # State update handled in loop

    def _update_loop(self):
        """
        Manages the state of the relay pin based on priority:
        1. Momentary Button (Overrides everything -> HIGH)
        2. Blink Toggle (Toggles High/Low)
        3. Default (LOW)
        """
        blink_state = False
        last_blink_time = 0.0

        while True:
            now = time.monotonic()

            # Blink logic (0.5s interval)
            if now - last_blink_time > 0.5:
                blink_state = not blink_state
                last_blink_time = now

            # Determine target output
            target_high = False

            with self.state.lock:
                momentary = self.state.momentary_active
                blinking = self.state.blink_active

            if momentary:
                target_high = True
            elif blinking:
                target_high = blink_state
            else:
                target_high = False

            # Apply output
            if GPIO_AVAILABLE:
                GPIO.output(PIN_RELAY_MOMENTARY, GPIO.HIGH if target_high else GPIO.LOW)

            with self.state.lock:
                self.state.relay_state = target_high

            time.sleep(0.05) # 20Hz update rate

    def cleanup(self):
        if GPIO_AVAILABLE:
            if self.pwm1: self.pwm1.stop()
            if self.pwm2: self.pwm2.stop()
            GPIO.cleanup()

def bridge_loop(state: SharedState, args):
    """
    Main relay loop: UDP iBUS receiver → UART TX | UART RX → Logs
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.listen_ip, args.listen_port))
        sock.setblocking(False)
        print(f"✓ UDP bound to {args.listen_ip}:{args.listen_port}")
        state.add_log("PI", f"UDP listening {args.listen_ip}:{args.listen_port}")
    except OSError as e:
        print(f"✗ FATAL: Cannot bind UDP {args.listen_ip}:{args.listen_port}: {e}")
        state.add_log("PI", f"ERROR: UDP bind failed: {e}")
        return

    # ── UART setup ──────────────────────────────────────────────────────────────
    uart_dev = None
    try:
        uart_dev = serial.Serial(args.uart_port, args.baud, timeout=0)
        print(f"✓ UART open: {args.uart_port} @ {args.baud}")
        state.add_log("PI", f"UART open {args.uart_port} @ {args.baud}")
        with state.lock:
            state.uart_open = True
    except Exception as e:
        print(f"✗ WARNING: Cannot open UART {args.uart_port}: {e}")
        state.add_log("PI", f"UART error: {e}")
        with state.lock:
            state.uart_open = False

    last_rx_time = 0.0
    last_no_rx_log_sec = -10
    uart_line_buf = bytearray()

    print(f"✓ Bridge loop running – awaiting raw iBUS frames on UDP {args.listen_port}...")

    while True:
        # Read incoming UDP packets (raw 32-byte iBUS frames)
        readable, _, _ = select.select([sock], [], [], 0.02)
        if readable:
            try:
                data, addr = sock.recvfrom(2048)
                if data:
                    now = time.monotonic()
                    last_rx_time = now

                    sender = f"{addr[0]}:{addr[1]}"
                    
                    with state.lock:
                        state.last_rc_time = now
                        state.last_rc_sender = sender
                        state.packets_rx += 1

                    # Forward raw 32-byte binary iBUS frame directly to ESP32.
                    # IBusBM on the ESP32 expects binary iBUS protocol, not ASCII text.
                    if uart_dev:
                        try:
                            if len(data) == IBUS_FRAME_LEN and data[:2] == IBUS_HEADER:
                                uart_dev.write(data)
                                with state.lock:
                                    state.packets_uart_tx += 1
                        except Exception:
                            pass  # Suppress serial disconnected errors to prevent spam

                    if state.packets_rx % 50 == 0:
                        state.add_log("RC", f"Relayed 50 iBUS frames to ESP32")
                    if state.packets_rx == 1:
                        print(f"✓ First raw iBUS packet received from {addr[0]}")
                        state.add_log("RC", f"First UDP frame from {sender}")
            except Exception as e:
                state.add_log("PI", f"UDP receive error: {e}")

        # Read incoming UART logs from ESP32
        if uart_dev:
            try:
                available = uart_dev.in_waiting
                if available > 0:
                    chunk = uart_dev.read(available)
                    for b in chunk:
                        if b == ord('\n'):
                            line_str = uart_line_buf.decode('ascii', errors='replace').strip()
                            if line_str:
                                state.add_log("ESP32", line_str)
                                with state.lock:
                                    state.uart_rx_lines += 1
                            uart_line_buf.clear()
                        elif b != ord('\r'):
                            uart_line_buf.append(b)
                            if len(uart_line_buf) > 256:
                                uart_line_buf.clear()  # Prevent memory leak on missing newlines
            except Exception:
                pass

        now_mono = time.monotonic()
        if last_rx_time == 0:
            bucket = int(now_mono) // 5
            if bucket != last_no_rx_log_sec:
                last_no_rx_log_sec = bucket
                state.add_log("PI", "⚠ Waiting for first RC frame from PC sender...")
        elif (now_mono - last_rx_time) > 0.5:
            # RC signal lost – stop forwarding frames.
            # ESP32 IBusBM has a built-in RC_LOST_US (500 ms) watchdog;
            # it enters failsafe automatically when frames stop arriving.
            pass
            bucket = int(now_mono) // 5
            if bucket != last_no_rx_log_sec:
                last_no_rx_log_sec = bucket
                state.add_log("PI", "⚠ RC signal lost – sent NO_SIGNAL to ESP32")

class CameraSource:
    def __init__(self, state: SharedState):
        self.state = state
        self.lock = threading.Lock()
        self.latest_jpeg = None
        self.running = True
        self.process = None

    def start_pipeline(self):
        # Detect camera command
        cmd_bin = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")

        if not cmd_bin:
            self.state.add_log("CAM", "ERROR: No libcamera-vid/rpicam-vid found!")
            with self.state.lock: self.state.camera_ok = False
            return False

        # Max Wide Angle Configuration:
        # We request 1296x972 (4:3 aspect ratio) to ensure full sensor readout
        # without 16:9 cropping. Mode 4 on v2 cameras is 1640x1232 (also 4:3).
        # Specifying a resolution close to this ratio usually gets the best FoV.
        cmd = [
            cmd_bin,
            "--codec", "mjpeg",
            "--width", "1296",      # 4:3 ratio for max vertical FoV
            "--height", "972",
            "--framerate", "30",
            "--timeout", "0",
            "--nopreview",
            "--output", "-",
            "--denoise", "cdn_off"  # Performance optimization
        ]

        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**8)
            self.state.add_log("CAM", f"Pipeline started: {cmd_bin} (Max FoV)")
            with self.state.lock:
                self.state.camera_ok = True

            # Monitor stderr in a separate thread to log camera errors
            def monitor_stderr(proc):
                for line in iter(proc.stderr.readline, b''):
                    line_str = line.decode('utf-8', errors='replace').strip()
                    if "error" in line_str.lower() or "fail" in line_str.lower():
                        self.state.add_log("CAM", f"STDERR: {line_str}")

            threading.Thread(target=monitor_stderr, args=(self.process,), daemon=True).start()

            return True
        except Exception as e:
            self.state.add_log("CAM", f"Failed to start camera: {e}")
            with self.state.lock:
                self.state.camera_ok = False
            return False

    def run(self):
        buf = b''
        while self.running:
            if self.process is None or self.process.poll() is not None:
                buf = b''
                if not self.start_pipeline():
                    time.sleep(2.0)
                    continue

            try:
                # Read in chunks for much better throughput than byte-by-byte
                chunk = self.process.stdout.read1(65536)
                if not chunk:
                    with self.state.lock:
                        self.state.camera_ok = False
                    self.process = None
                    continue

                buf += chunk

                # Extract all complete JPEG frames (SOI→EOI); keep the latest
                latest = None
                while True:
                    soi = buf.find(b'\xff\xd8')
                    if soi == -1:
                        buf = b''
                        break
                    eoi = buf.find(b'\xff\xd9', soi + 2)
                    if eoi == -1:
                        buf = buf[soi:]  # keep partial frame for next read
                        break
                    eoi += 2  # include the EOI marker bytes
                    latest = buf[soi:eoi]
                    buf = buf[eoi:]

                if latest is not None:
                    with self.lock:
                        self.latest_jpeg = latest
                    with self.state.lock:
                        self.state.camera_ok = True

                # Prevent unbounded buffer growth
                if len(buf) > 5 * 1024 * 1024:
                    buf = b''

            except Exception:
                with self.state.lock:
                    self.state.camera_ok = False
                time.sleep(0.1)

    def get_jpeg(self):
        with self.lock:
            return self.latest_jpeg

def create_app(state: SharedState, cam: CameraSource, gpio: GpioController, args):
    app = Flask(__name__)

    @app.get("/")
    def dashboard():
        return render_template_string(DASHBOARD_HTML)

    @app.get("/api/status")
    def api_status():
        with state.lock:
            last_age = (time.monotonic() - state.last_rc_time) if state.last_rc_time > 0 else 9999.0
            payload = {
                "ethernet_up": check_ethernet_up(args.eth_interface),
                "link_alive": last_age < 1.0,
                "last_rc_age_sec": round(last_age, 3),
                "packets_rx": state.packets_rx,
                "packets_uart_tx": state.packets_uart_tx,
                "uart_rx_lines": state.uart_rx_lines,
                "uart_open": state.uart_open,
                "last_rc_sender": state.last_rc_sender,
                "camera_ok": state.camera_ok,
                "blink_active": state.blink_active,
                "relay_state": state.relay_state,
            }
        return jsonify(payload)

    @app.get("/api/logs")
    def api_logs():
        from flask import request as flask_request
        try:
            since = int(flask_request.args.get("since", "0"))
        except ValueError:
            since = 0
        return jsonify({"logs": state.get_logs_since(since)})

    @app.post("/api/servo/<int:id>")
    def set_servo(id):
        data = request.json
        angle = data.get("angle", 90)
        gpio.set_servo(id, angle)
        return jsonify({"status": "ok", "id": id, "angle": angle})

    @app.post("/api/gpio/momentary")
    def gpio_momentary():
        data = request.json
        active = data.get("active", False)
        gpio.set_momentary(active)
        return jsonify({"status": "ok", "momentary": active})

    @app.post("/api/gpio/blink")
    def gpio_blink():
        data = request.json
        active = data.get("active", False)
        gpio.set_blink(active)
        return jsonify({"status": "ok", "blink": active})

    @app.get("/video_feed")
    def video_feed():
        def generate():
            while True:
                jpeg = cam.get_jpeg()
                if jpeg is None:
                    time.sleep(0.1)
                    continue
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
                time.sleep(1 / 30)
        return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app

def main():
    parser = argparse.ArgumentParser(description="Pi Transparent UDP-to-UART Relay")
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=5000)
    parser.add_argument("--uart-port", default="/dev/serial0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--eth-interface", default="eth0")
    parser.add_argument("--web-host", default="0.0.0.0")
    parser.add_argument("--web-port", type=int, default=8080)
    args = parser.parse_args()

    print("\\n" + "="*60)
    print("UNDERWATER ROVER SYSTEM (TRANSPARENT UART RELAY) - STARTING")
    print("="*60)
    print(f"\\n📡 NETWORK:")
    print(f"   UDP Listen:   {args.listen_ip}:{args.listen_port}")
    print(f"   Dashboard:    http://0.0.0.0:{args.web_port}")
    print(f"\\n⚡ UART:")
    print(f"   Port:         {args.uart_port} @ {args.baud} baud")
    print("="*60 + "\\n")

    state = SharedState()
    gpio = GpioController(state)
    cam = CameraSource(state)

    # Start System Monitor
    sys_mon = SystemMonitor(state)

    bridge_thread = threading.Thread(target=bridge_loop, args=(state, args), daemon=True)
    cam_thread = threading.Thread(target=cam.run, daemon=True)

    bridge_thread.start()
    cam_thread.start()
    
    time.sleep(0.5)
    app = create_app(state, cam, gpio, args)

    try:
        app.run(host=args.web_host, port=args.web_port, debug=False, threaded=True, use_reloader=False)
    finally:
        gpio.cleanup()

if __name__ == "__main__":
    main()