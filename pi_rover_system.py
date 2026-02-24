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
  <title>Rover Dashboard (UART Relay)</title>
  <style>
    body { font-family: 'Segoe UI', Arial, sans-serif; background:#10131a; color:#e8ecf3; margin:0; }
    .wrap { display:grid; grid-template-columns: 2fr 1fr; gap:16px; padding:16px; height: 95vh; box-sizing: border-box; }
    .card { background:#1a2030; border:1px solid #2a344d; border-radius:10px; padding:12px; margin-bottom:16px; overflow: hidden; display: flex; flex-direction: column; }
    h1 { margin:0 0 12px 0; font-size:20px; color: #fff; }
    h2 { margin:0 0 10px 0; font-size:16px; color: #8a9bbd; border-bottom: 1px solid #2a344d; padding-bottom: 5px; }
    .mono { font-family: 'Consolas', 'Monaco', monospace; white-space: pre-wrap; word-break: break-word; font-size: 13px; }
    .ok { color:#7CFC9A; }
    .bad { color:#ff7d7d; }
    .warn { color:#FFB74D; }
    img { width:100%; border-radius:8px; border:1px solid #2a344d; background:#000; flex-grow: 1; object-fit: contain; }

    /* GPIO Controls */
    .control-row { display: flex; align-items: center; margin-bottom: 10px; }
    .control-label { width: 80px; font-weight: bold; }
    input[type=range] { flex-grow: 1; margin: 0 10px; }
    button {
      background: #2a344d; color: white; border: 1px solid #4a5a7d;
      padding: 8px 16px; border-radius: 4px; cursor: pointer;
      font-weight: bold; transition: background 0.2s;
    }
    button:active { background: #4a5a7d; transform: translateY(1px); }
    button.active { background: #7CFC9A; color: #1a2030; }

    .switch { position: relative; display: inline-block; width: 50px; height: 24px; }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider {
      position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
      background-color: #4a5a7d; transition: .4s; border-radius: 24px;
    }
    .slider:before {
      position: absolute; content: ""; height: 16px; width: 16px; left: 4px; bottom: 4px;
      background-color: white; transition: .4s; border-radius: 50%;
    }
    input:checked + .slider { background-color: #2196F3; }
    input:checked + .slider:before { transform: translateX(26px); }

    .gpio-section { margin-top: 20px; border-top: 1px solid #2a344d; padding-top: 10px; }
    .gpio-title { font-size: 14px; color: #8a9bbd; margin-bottom: 10px; }

    /* Telemetry Log Console */
    .log-container { flex-grow: 1; display: flex; flex-direction: column; overflow: hidden; }
    .log-controls { display: flex; gap: 10px; margin-bottom: 8px; font-size: 12px; flex-wrap: wrap; }
    .log-filter { display: flex; align-items: center; gap: 4px; cursor: pointer; user-select: none; }
    .log-filter input { margin: 0; }
    #logs {
        background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
        padding: 8px; overflow-y: scroll; flex-grow: 1; font-size: 12px; line-height: 1.4;
    }
    .log-entry { display: flex; gap: 8px; border-bottom: 1px solid #21262d; padding: 2px 0; }
    .log-ts { color: #8b949e; min-width: 65px; }
    .log-src { font-weight: bold; min-width: 50px; }
    .log-msg { color: #c9d1d9; flex-grow: 1; }

    /* Log Colors */
    .src-ESP32 { color: #79c0ff; }
    .src-PI { color: #d2a8ff; }
    .src-RC { color: #7ee787; }
    .src-GPIO { color: #ffa657; }
    .src-SYS { color: #ff7b72; }

    .msg-warn { color: #e3b341; }
    .msg-error { color: #ff7b72; font-weight: bold; }
    .msg-info { color: #a5d6ff; }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: #1a2030; }
    ::-webkit-scrollbar-thumb { background: #4a5a7d; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #5a6a8d; }
  </style>
</head>
<body>
  <div class="wrap">
    <!-- Left Column: Camera + GPIO -->
    <div style="display:flex; flex-direction:column; gap:16px;">
      <div class="card" style="flex-grow:1;">
        <h1>Underwater Rover Dashboard</h1>
        <h2>Live Camera</h2>
        <img src="/video_feed" alt="camera" onerror="this.src='data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdib3g9IjAgMCAxMDAgMTAwIj48cmVjdCB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgZmlsbD0iIzEwMTMxYSIvPjx0ZXh0IHg9IjUwIiB5PSI1MCIgZmlsbD0iIzRhNWE3ZCIgZm9udC1zaXplPSIxMCIgdGV4dC1hbmNob3I9Im1pZGRsZSI+Q2FtZXJhIE9mZmxpbmU8L3RleHQ+PC9zdmc+'" />
      </div>

      <div class="card">
        <h2>GPIO Controls</h2>

        <div class="control-row">
          <div class="control-label">Servo 1</div>
          <input type="range" min="0" max="180" value="90" oninput="updateServo(1, this.value)">
          <span id="val-s1">90°</span>
        </div>

        <div class="control-row">
          <div class="control-label">Servo 2</div>
          <input type="range" min="0" max="180" value="90" oninput="updateServo(2, this.value)">
          <span id="val-s2">90°</span>
        </div>

        <div class="gpio-section">
            <div class="gpio-title">Relay Control (GPIO 26)</div>
            <div class="control-row" style="justify-content: space-around;">
              <button onmousedown="momentary(true)" onmouseup="momentary(false)" onmouseleave="momentary(false)">
                HOLD: HIGH
              </button>

              <div style="display:flex; align-items:center;">
                <span style="margin-right:10px;">Blink Mode:</span>
                <label class="switch">
                  <input type="checkbox" id="chk-blink" onchange="toggleBlink(this.checked)">
                  <span class="slider"></span>
                </label>
              </div>
            </div>
        </div>
      </div>
    </div>

    <!-- Right Column: Status + Logs -->
    <div style="display:flex; flex-direction:column; gap:16px; height:100%;">
      <div class="card">
        <h2>System Status</h2>
        <div id="status" class="mono" style="font-size:12px;">loading...</div>
      </div>
      <div class="card" style="flex-grow:1;">
        <h2>Telemetry & Debug Logs</h2>
        <div class="log-container">
            <div class="log-controls">
                <label class="log-filter"><input type="checkbox" checked onchange="toggleSrc('ESP32')" id="f-ESP32"> ESP32</label>
                <label class="log-filter"><input type="checkbox" checked onchange="toggleSrc('PI')" id="f-PI"> PI</label>
                <label class="log-filter"><input type="checkbox" checked onchange="toggleSrc('RC')" id="f-RC"> RC</label>
                <label class="log-filter"><input type="checkbox" checked onchange="toggleSrc('SYS')" id="f-SYS"> SYS</label>
                <button onclick="clearLogs()" style="padding:2px 8px; font-size:10px; margin-left:auto;">Clear</button>
                <button onclick="toggleScroll()" id="btn-scroll" style="padding:2px 8px; font-size:10px;" class="active">Auto-Scroll</button>
            </div>
            <div id="logs" class="mono"></div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let lastId = 0;
    let autoScroll = true;
    const filterState = { ESP32: true, PI: true, RC: true, SYS: true, GPIO: true };

    async function refreshStatus() {
      const r = await fetch('/api/status');
      const s = await r.json();
      const healthy = s.link_alive ? 'ok' : 'bad';
      document.getElementById('status').innerHTML =
        `RC link: <span class="${healthy}">${s.link_alive ? 'LIVE' : 'LOST'}</span> (${s.last_rc_age_sec.toFixed(2)}s ago)\n` +
        `Sender: ${s.last_rc_sender || '-'}\n` +
        `UDP RX: ${s.packets_rx} | UART TX: ${s.packets_uart_tx}\n` +
        `Relay State: <span class="${s.relay_state ? 'ok' : ''}">${s.relay_state ? 'HIGH' : 'LOW'}</span> (Blink: ${s.blink_active ? 'ON' : 'OFF'})\n` +
        `Camera: <span class="${s.camera_ok ? 'ok' : 'bad'}">${s.camera_ok ? 'OK' : 'OFF'}</span>\n` +
        `Ethernet: <span class="${s.ethernet_up ? 'ok' : 'bad'}">${s.ethernet_up ? 'UP' : 'DOWN'}</span>`;
    }

    async function refreshLogs() {
      const r = await fetch('/api/logs?since=' + lastId);
      const data = await r.json();
      const box = document.getElementById('logs');

      data.logs.forEach(item => {
        lastId = item.id;

        const row = document.createElement('div');
        row.className = `log-entry row-${item.src}`;

        // Detect log level
        let msgClass = '';
        if (item.msg.includes('ERROR') || item.msg.includes('FAIL') || item.msg.includes('✗')) msgClass = 'msg-error';
        else if (item.msg.includes('WARN') || item.msg.includes('⚠')) msgClass = 'msg-warn';
        else if (item.msg.includes('INFO') || item.msg.includes('✓')) msgClass = 'msg-info';

        row.innerHTML = `
            <span class="log-ts">${item.ts}</span>
            <span class="log-src src-${item.src}">${item.src}</span>
            <span class="log-msg ${msgClass}">${item.msg}</span>
        `;

        // Apply initial visibility based on filter
        if (!filterState[item.src] && filterState[item.src] !== undefined) {
            row.style.display = 'none';
        }

        box.appendChild(row);
      });

      // Cleanup old logs if too many (keep last 500 DOM elements)
      while (box.children.length > 500) {
        box.removeChild(box.firstChild);
      }

      if (autoScroll && data.logs.length > 0) {
        box.scrollTop = box.scrollHeight;
      }
    }

    function toggleSrc(src) {
        filterState[src] = document.getElementById('f-' + src).checked;
        const rows = document.querySelectorAll('.row-' + src);
        rows.forEach(r => r.style.display = filterState[src] ? 'flex' : 'none');
    }

    function toggleScroll() {
        autoScroll = !autoScroll;
        const btn = document.getElementById('btn-scroll');
        if (autoScroll) {
            btn.classList.add('active');
            const box = document.getElementById('logs');
            box.scrollTop = box.scrollHeight;
        } else {
            btn.classList.remove('active');
        }
    }

    function clearLogs() {
        document.getElementById('logs').innerHTML = '';
    }

    function updateServo(id, angle) {
      document.getElementById('val-s' + id).innerText = angle + '°';
      fetch(`/api/servo/${id}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({angle: parseInt(angle)})
      });
    }

    function momentary(state) {
      fetch('/api/gpio/momentary', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({active: state})
      });
    }

    function toggleBlink(state) {
      fetch('/api/gpio/blink', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({active: state})
      });
    }

    setInterval(refreshStatus, 400);
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
        cmd = [
            "rpicam-vid",
            "--codec", "mjpeg",
            # Full-sensor binned mode → maximum wide-angle field of view
            "--mode", "4",          # Mode 4: 1640x1232 full-frame (Pi Cam v2/HQ)
            "--width", "960",       # Scale down for network streaming
            "--height", "720",
            "--framerate", "30",
            "--timeout", "0",
            "--nopreview",
            "--output", "-",
        ]
        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**8)
            self.state.add_log("CAM", "rpicam-vid pipeline started")
            with self.state.lock:
                self.state.camera_ok = True
            return True
        except Exception as e:
            self.state.add_log("CAM", f"Failed to start camera: {e}")
            with self.state.lock:
                self.state.camera_ok = False
            return False

    def run(self):
        while self.running:
            if self.process is None or self.process.poll() is not None:
                if not self.start_pipeline():
                    time.sleep(2.0)
                    continue

            try:
                byte1 = self.process.stdout.read(1)
                byte2 = self.process.stdout.read(1)
                while byte1 != b'\xff' or byte2 != b'\xd8':
                    byte1 = byte2
                    byte2 = self.process.stdout.read(1)
                    if not byte2: break
                if not byte2: continue
                
                jpeg_data = b'\xff\xd8'
                byte1 = self.process.stdout.read(1)
                byte2 = self.process.stdout.read(1)
                jpeg_data += byte1 + byte2
                
                while byte1 != b'\xff' or byte2 != b'\xd9':
                    byte1 = byte2
                    byte2 = self.process.stdout.read(1)
                    if not byte2: break
                    jpeg_data += byte2
                    
                if byte2:
                    with self.lock:
                        self.latest_jpeg = jpeg_data
                    with self.state.lock:
                        self.state.camera_ok = True
                else:
                    with self.state.lock:
                        self.state.camera_ok = False
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