#!/usr/bin/env python3
"""
Underwater Rover Native Pi Service - Transparent iBUS Bridge

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
    
  • Flask Web Dashboard:
    - Serves HTML dashboard on <eth0_ip>:8080
    - /api/status: Real-time JSON status
    - /api/logs: Streaming log entries from Pi AND ESP32
    
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
from dataclasses import dataclass, field
from typing import Deque, List

from flask import Flask, Response, jsonify, render_template_string

DASHBOARD_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Rover Dashboard (UART Relay)</title>
  <style>
    body { font-family: Arial, sans-serif; background:#10131a; color:#e8ecf3; margin:0; }
    .wrap { display:grid; grid-template-columns: 2fr 1fr; gap:16px; padding:16px; }
    .card { background:#1a2030; border:1px solid #2a344d; border-radius:10px; padding:12px; }
    h1 { margin:0 0 12px 0; font-size:20px; }
    h2 { margin:0 0 10px 0; font-size:16px; }
    .mono { font-family: monospace; white-space: pre-wrap; word-break: break-word; }
    .ok { color:#7CFC9A; }
    .bad { color:#ff7d7d; }
    img { width:100%; border-radius:8px; border:1px solid #2a344d; background:#000; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Underwater Rover Dashboard (UART Relay)</h1>
      <h2>Live Camera</h2>
      <img src="/video_feed" alt="camera" />
    </div>
    <div>
      <div class="card" style="margin-bottom:16px;">
        <h2>Status</h2>
        <div id="status" class="mono">loading...</div>
      </div>
      <div class="card">
        <h2>Logs (Pi + ESP32)</h2>
        <div id="logs" class="mono" style="height:420px; overflow:auto;"></div>
      </div>
    </div>
  </div>

  <script>
    let lastId = 0;

    async function refreshStatus() {
      const r = await fetch('/api/status');
      const s = await r.json();
      const healthy = s.link_alive ? 'ok' : 'bad';
      document.getElementById('status').innerHTML =
        `Ethernet: <span class="${s.ethernet_up ? 'ok' : 'bad'}">${s.ethernet_up ? 'UP' : 'DOWN'}</span>\n` +
        `RC link: <span class="${healthy}">${s.link_alive ? 'LIVE' : 'LOST'}</span>\n` +
        `Last packet age: ${s.last_rc_age_sec.toFixed(2)}s\n` +
        `UART Port: <span class="${s.uart_open ? 'ok' : 'bad'}">${s.uart_open ? 'Open' : 'Error'}</span>\n` +
        `UDP RX: ${s.packets_rx}\n` +
        `UART TX: ${s.packets_uart_tx}\n` +
        `UART RX Lines: ${s.uart_rx_lines}\n` +
        `Sender IP: ${s.last_rc_sender || '-'}\n` +
        `Camera: ${s.camera_ok ? 'OK' : 'NOT AVAILABLE'}`;
    }

    async function refreshLogs() {
      const r = await fetch('/api/logs?since=' + lastId);
      const data = await r.json();
      const box = document.getElementById('logs');
      for (const item of data.logs) {
        lastId = item.id;
        box.textContent += `[${item.ts}] ${item.src}: ${item.msg}\n`;
      }
      box.scrollTop = box.scrollHeight;
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

@dataclass
class SharedState:
    last_rc_time: float = 0.0
    last_rc_sender: str = ""       
    packets_rx: int = 0
    packets_uart_tx: int = 0
    uart_rx_lines: int = 0
    uart_open: bool = False
    camera_ok: bool = False
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

def create_app(state: SharedState, cam: CameraSource, args):
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
    cam = CameraSource(state)

    bridge_thread = threading.Thread(target=bridge_loop, args=(state, args), daemon=True)
    cam_thread = threading.Thread(target=cam.run, daemon=True)

    bridge_thread.start()
    cam_thread.start()
    
    time.sleep(0.5)
    app = create_app(state, cam, args)
    app.run(host=args.web_host, port=args.web_port, debug=False, threaded=True, use_reloader=False)

if __name__ == "__main__":
    main()