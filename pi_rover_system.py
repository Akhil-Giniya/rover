#!/usr/bin/env python3
"""
Underwater Rover Native Pi Service - Transparent iBUS Bridge + GPIO Control + Telemetry

This module implements the core rover relay system that runs on Raspberry Pi 4B:

ARCHITECTURE:
  • Bridge Loop (UDP Receiver ↔ UART):
    - Binds UDP socket on 0.0.0.0:5000 (listens for raw iBUS frames from PC)
    - Forwards exact 32-byte binary iBUS frames directly to /dev/serial0 (ESP32)
    - Reads UART output from ESP32 and streams it to the web dashboard logs
    
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
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Deque, List, Optional

from flask import Flask, jsonify, render_template_string, request

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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg1:#0a0a1a;
      --glass:rgba(255,255,255,0.04);
      --glass-b:rgba(255,255,255,0.09);
      --gp:linear-gradient(135deg,#7c3aed,#06b6d4);
      --success:#10b981;--warning:#f59e0b;--danger:#ef4444;
      --text:#f1f5f9;--muted:#64748b;
      --glp:rgba(124,58,237,0.35);--glc:rgba(6,182,212,0.35);
    }
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{
      font-family:'Space Grotesk',sans-serif;
      background:var(--bg1);
      background-image:
        radial-gradient(ellipse 80% 50% at 15% 10%,rgba(124,58,237,.18) 0%,transparent 60%),
        radial-gradient(ellipse 60% 40% at 85% 90%,rgba(6,182,212,.14) 0%,transparent 60%),
        radial-gradient(ellipse 50% 70% at 50% 50%,rgba(15,10,40,.9) 0%,transparent 100%);
      color:var(--text);height:100vh;overflow:hidden;
    }
    .wrap{display:grid;grid-template-columns:270px 1fr 290px;grid-template-rows:64px 1fr;gap:12px;padding:12px;height:100vh;}
    .card{background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);border:1px solid var(--glass-b);border-radius:18px;padding:18px;display:flex;flex-direction:column;transition:border-color .3s,box-shadow .3s;position:relative;overflow:hidden;}
    .card::before{content:'';position:absolute;inset:0;border-radius:inherit;background:linear-gradient(135deg,rgba(255,255,255,.05) 0%,transparent 60%);pointer-events:none;}
    .card:hover{border-color:rgba(124,58,237,.3);box-shadow:0 0 30px rgba(124,58,237,.1)}
    .header{grid-column:1/-1;flex-direction:row;align-items:center;justify-content:space-between;padding:10px 18px;background:rgba(255,255,255,.025);border-color:rgba(255,255,255,.06);}
    .brand{display:flex;align-items:center;gap:12px}
    .brand-icon{width:38px;height:38px;border-radius:10px;background:var(--gp);display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 0 20px var(--glp);}
    .brand-name{font-size:16px;font-weight:700;letter-spacing:.5px;background:var(--gp);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
    .brand-sub{font-size:9px;color:var(--muted);letter-spacing:2px;text-transform:uppercase}
    .hdr-right{display:flex;align-items:center;gap:16px}
    .sys-time{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--muted)}
    .sbadge{display:flex;align-items:center;gap:6px;padding:6px 14px;border-radius:999px;font-size:10px;font-weight:700;letter-spacing:1.5px;background:rgba(239,68,68,.12);color:var(--danger);border:1px solid rgba(239,68,68,.25);transition:all .4s;}
    .sbadge.live{background:rgba(16,185,129,.12);color:var(--success);border-color:rgba(16,185,129,.3);box-shadow:0 0 18px rgba(16,185,129,.2);}
    .led{width:7px;height:7px;border-radius:50%;background:var(--danger);box-shadow:0 0 8px var(--danger)}
    .sbadge.live .led{background:var(--success);box-shadow:0 0 8px var(--success);animation:lp 1.5s ease-in-out infinite}
    @keyframes lp{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.8)}}
    .stitle{font-size:10px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:var(--muted);display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;}
    .stitle::before{content:'';display:inline-block;width:3px;height:12px;border-radius:2px;background:var(--gp);margin-right:8px;box-shadow:0 0 8px var(--glp);}
    .stat-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
    .stat-box{background:rgba(255,255,255,.03);border:1px solid var(--glass-b);border-radius:12px;padding:12px;text-align:center;transition:transform .2s,border-color .2s;}
    .stat-box:hover{transform:translateY(-2px);border-color:rgba(124,58,237,.3)}
    .stat-val{font-size:20px;font-weight:700;background:var(--gp);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;animation:sh 3s ease-in-out infinite;}
    @keyframes sh{0%,100%{filter:brightness(1)}50%{filter:brightness(1.4)}}
    .stat-lbl{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-top:4px}
    .titem{margin-bottom:10px}
    .thdr{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-bottom:4px;letter-spacing:1px}
    .ttrack{height:5px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden}
    .tbar{height:100%;border-radius:3px;background:var(--gp);box-shadow:0 0 8px var(--glp);transition:width .8s cubic-bezier(.4,0,.2,1)}
    .cgrp{margin-bottom:16px}
    .clbl{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:var(--muted);margin-bottom:8px;letter-spacing:1.5px}
    .cval{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:600;background:var(--gp);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
    input[type=range]{-webkit-appearance:none;width:100%;background:transparent;cursor:pointer}
    input[type=range]::-webkit-slider-runnable-track{height:5px;border-radius:3px;background:linear-gradient(90deg,rgba(124,58,237,.5),rgba(6,182,212,.5))}
    input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:#fff;margin-top:-6.5px;box-shadow:0 0 12px var(--glp);transition:transform .15s,box-shadow .15s}
    input[type=range]:hover::-webkit-slider-thumb{transform:scale(1.25);box-shadow:0 0 22px var(--glc)}
    .rpanel{background:rgba(0,0,0,.2);border:1px solid var(--glass-b);border-radius:12px;padding:14px}
    .rlbl{font-size:9px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-bottom:10px}
    .btn-m{width:100%;padding:13px;background:var(--gp);border:none;border-radius:10px;color:#fff;font-family:'Space Grotesk',sans-serif;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;cursor:pointer;box-shadow:0 0 22px var(--glp),0 4px 15px rgba(0,0,0,.3);transition:transform .15s,box-shadow .15s,filter .15s;position:relative;overflow:hidden;}
    .btn-m::after{content:'';position:absolute;inset:0;border-radius:inherit;background:linear-gradient(135deg,rgba(255,255,255,.15),transparent);pointer-events:none}
    .btn-m:hover{transform:translateY(-2px);box-shadow:0 0 38px var(--glp),0 8px 20px rgba(0,0,0,.4)}
    .btn-m:active{transform:scale(.97);filter:brightness(.9)}
    .sw-row{display:flex;justify-content:space-between;align-items:center;margin-top:12px}
    .sw-lbl{font-size:10px;color:var(--muted);letter-spacing:1px}
    .sw{position:relative;width:46px;height:26px}
    .sw input{opacity:0;width:0;height:0}
    .tog{position:absolute;inset:0;cursor:pointer;background:rgba(255,255,255,.08);border-radius:26px;border:1px solid var(--glass-b);transition:background .3s,box-shadow .3s}
    .tog::before{content:'';position:absolute;width:20px;height:20px;left:2px;top:2px;background:#fff;border-radius:50%;transition:transform .3s,box-shadow .3s}
    input:checked+.tog{background:linear-gradient(135deg,rgba(124,58,237,.6),rgba(6,182,212,.6));border-color:rgba(124,58,237,.5);box-shadow:0 0 14px var(--glp)}
    input:checked+.tog::before{transform:translateX(20px);box-shadow:0 0 8px var(--glc)}
    .cam-wrap{flex:1;min-height:0;background:#000;border-radius:10px;overflow:hidden;position:relative;border:1px solid var(--glass-b)}
    .cam-wrap img{width:100%;height:100%;object-fit:contain;display:block}
    .cam-hud{position:absolute;top:10px;right:10px;display:flex;align-items:center;gap:8px}
    .cam-st{display:flex;align-items:center;gap:6px;background:rgba(0,0,0,.6);backdrop-filter:blur(8px);padding:5px 12px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:1px;border:1px solid rgba(255,255,255,.1)}
    .cam-dot{width:7px;height:7px;border-radius:50%;background:var(--success);box-shadow:0 0 8px var(--success);animation:lp 1.5s infinite}
    .cam-err .cam-dot{background:var(--danger);box-shadow:0 0 8px var(--danger);animation:none}
    .btn-rc{background:rgba(124,58,237,.2);border:1px solid rgba(124,58,237,.4);color:#a78bfa;padding:5px 12px;border-radius:8px;font-size:10px;font-weight:600;letter-spacing:1px;cursor:pointer;backdrop-filter:blur(8px);transition:background .2s,box-shadow .2s}
    .btn-rc:hover{background:rgba(124,58,237,.4);box-shadow:0 0 12px var(--glp)}
    .console{font-family:'JetBrains Mono',monospace;font-size:11px;background:rgba(0,0,0,.45);border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:10px;flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:3px}
    .console::-webkit-scrollbar{width:4px}
    .console::-webkit-scrollbar-track{background:transparent}
    .console::-webkit-scrollbar-thumb{background:rgba(124,58,237,.4);border-radius:2px}
    .log-entry{display:flex;gap:8px;opacity:.85;align-items:baseline;line-height:1.5}
    .log-entry:hover{opacity:1}
    .ts{color:#445566;min-width:56px;font-size:10px}
    .tag{font-weight:700;border-radius:4px;padding:1px 5px;min-width:40px;text-align:center;font-size:9px;letter-spacing:1px}
    .tag-ESP32{background:rgba(59,130,246,.15);color:#60a5fa;border:1px solid rgba(59,130,246,.2)}
    .tag-PI{background:rgba(167,139,250,.15);color:#a78bfa;border:1px solid rgba(167,139,250,.2)}
    .tag-RC{background:rgba(52,211,153,.15);color:#34d399;border:1px solid rgba(52,211,153,.2)}
    .tag-SYS{background:rgba(251,146,60,.15);color:#fb923c;border:1px solid rgba(251,146,60,.2)}
    .tag-GPIO{background:rgba(245,158,11,.15);color:#fbbf24;border:1px solid rgba(245,158,11,.2)}
    .fbar{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
    .chip{display:flex;align-items:center;gap:5px;padding:4px 10px;border-radius:999px;cursor:pointer;font-size:10px;font-weight:600;letter-spacing:1px;background:rgba(255,255,255,.05);border:1px solid var(--glass-b);color:var(--muted);transition:all .2s;user-select:none}
    .chip:hover{background:rgba(124,58,237,.15);border-color:rgba(124,58,237,.3);color:#a78bfa}
    .chip input{display:none}
    .cdot{width:6px;height:6px;border-radius:50%}
    @media(max-width:1100px){
      .wrap{grid-template-columns:240px 1fr;grid-template-rows:auto 1fr 1fr;height:auto;overflow:auto}
      .header{grid-column:1/-1}
      .col-r{grid-column:1/-1}
    }
    @media(max-width:700px){.wrap{grid-template-columns:1fr}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="card header">
    <div class="brand">
      <div class="brand-icon">&#x1F916;</div>
      <div>
        <div class="brand-name">ROVER COMMAND CENTER</div>
        <div class="brand-sub">Underwater Systems Interface &#xB7; v2.0</div>
      </div>
    </div>
    <div class="hdr-right">
      <div class="sys-time" id="sys-time">--:--:--</div>
      <div id="link-badge" class="sbadge lost">
        <span class="led"></span>
        <span id="link-txt">SIGNAL LOST</span>
      </div>
    </div>
  </div>

  <div style="display:flex;flex-direction:column;gap:12px;overflow-y:auto;min-height:0;">
    <div class="card">
      <div class="stitle">Link Telemetry</div>
      <div class="stat-row">
        <div class="stat-box"><div id="stat-rc" class="stat-val">--</div><div class="stat-lbl">Last Packet</div></div>
        <div class="stat-box"><div id="stat-pps" class="stat-val">0</div><div class="stat-lbl">Packets/Sec</div></div>
      </div>
      <div class="titem">
        <div class="thdr"><span>Signal Strength</span><span id="sig-pct">0%</span></div>
        <div class="ttrack"><div class="tbar" id="sig-bar" style="width:0%"></div></div>
      </div>
    </div>
    <div class="card" style="flex:1">
      <div class="stitle">Hardware Control</div>
      <div class="cgrp">
        <div class="clbl"><span>SERVO 1</span><span id="val-s1" class="cval">90&deg;</span></div>
        <input type="range" min="0" max="180" value="90" oninput="updateServo(1,this.value)">
      </div>
      <div class="cgrp">
        <div class="clbl"><span>SERVO 2</span><span id="val-s2" class="cval">90&deg;</span></div>
        <input type="range" min="0" max="180" value="90" oninput="updateServo(2,this.value)">
      </div>
      <div class="rpanel">
        <div class="rlbl">Auxiliary Relay &middot; GPIO 26</div>
        <button class="btn-m" onmousedown="momentary(true)" onmouseup="momentary(false)" onmouseleave="momentary(false)">
          &#x26A1; Hold to Activate
        </button>
        <div class="sw-row">
          <span class="sw-lbl">Auto-Blink Mode</span>
          <label class="sw">
            <input type="checkbox" id="chk-blink" onchange="toggleBlink(this.checked)">
            <span class="tog"></span>
          </label>
        </div>
      </div>
    </div>
  </div>

  <div class="card" style="overflow:hidden;">
    <div class="stitle">
      Live Camera Feed
      <button class="btn-rc" onclick="reconnectCam()">&#x21BB; RECONNECT</button>
    </div>
    <div class="cam-wrap">
      <img id="cam-img" src="{{ video_url }}" alt="Camera feed" onerror="camError()" onload="camOk()" />
      <div class="cam-hud">
        <div id="cam-status-bar" class="cam-st">
          <div class="cam-dot" id="cam-dot"></div>
          <span id="cam-status-txt">LIVE</span>
        </div>
      </div>
    </div>
  </div>

  <div class="card col-r" style="overflow:hidden;">
    <div class="stitle">
      System Logs
      <button onclick="clearLogs()" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:10px;letter-spacing:1px;">&#x2715; CLEAR</button>
    </div>
    <div id="console" class="console"></div>
    <div class="fbar">
      <label class="chip"><input type="checkbox" checked onchange="toggleSrc('ESP32')" id="f-ESP32"><span class="cdot" style="background:#60a5fa"></span>ESP32</label>
      <label class="chip"><input type="checkbox" checked onchange="toggleSrc('PI')" id="f-PI"><span class="cdot" style="background:#a78bfa"></span>PI</label>
      <label class="chip"><input type="checkbox" checked onchange="toggleSrc('RC')" id="f-RC"><span class="cdot" style="background:#34d399"></span>RC</label>
      <label class="chip"><input type="checkbox" checked onchange="toggleSrc('SYS')" id="f-SYS"><span class="cdot" style="background:#fb923c"></span>SYS</label>
      <label class="chip"><input type="checkbox" checked onchange="toggleSrc('GPIO')" id="f-GPIO"><span class="cdot" style="background:#fbbf24"></span>GPIO</label>
    </div>
  </div>
</div>
<script>
  let lastId = 0;
  const filterState = { ESP32: true, PI: true, RC: true, SYS: true, GPIO: true };
  let lastPktCount = 0, lastTime = Date.now();

  function tickClock() {
    document.getElementById('sys-time').innerText = new Date().toLocaleTimeString('en-US',{hour12:false});
  }
  setInterval(tickClock, 1000); tickClock();

  async function refreshStatus() {
    try {
      const r = await fetch('/api/status');
      const s = await r.json();
      const badge = document.getElementById('link-badge');
      const txt = document.getElementById('link-txt');
      if (s.link_alive) { badge.className='sbadge live'; txt.innerText='LINK ACTIVE'; }
      else { badge.className='sbadge lost'; txt.innerText='SIGNAL LOST'; }
      document.getElementById('stat-rc').innerText = s.last_rc_age_sec < 900 ? s.last_rc_age_sec.toFixed(2)+'s' : '--';
      const now = Date.now();
      if (now - lastTime > 1000) {
        const pps = Math.round((s.packets_rx - lastPktCount)*1000/(now-lastTime));
        const safe = pps > 0 ? pps : 0;
        document.getElementById('stat-pps').innerText = safe;
        const pct = Math.min(100, Math.round(safe/60*100));
        document.getElementById('sig-bar').style.width = pct+'%';
        document.getElementById('sig-pct').innerText = pct+'%';
        lastPktCount = s.packets_rx; lastTime = now;
      }
    } catch(e) {}
  }

  async function refreshLogs() {
    try {
      const r = await fetch('/api/logs?since='+lastId);
      const data = await r.json();
      const box = document.getElementById('console');
      const atBottom = box.scrollHeight - box.scrollTop <= box.clientHeight + 5;
      data.logs.forEach(item => {
        lastId = item.id;
        const row = document.createElement('div');
        row.className = 'log-entry row-'+item.src;
        let color = '#94a3b8';
        if (item.msg.includes('ERR')||item.msg.includes('FAIL')) color = '#f87171';
        else if (item.msg.includes('WARN')) color = '#fbbf24';
        else if (item.msg.includes('OK')||item.msg.includes('open')) color = '#4ade80';
        row.innerHTML = '<span class="ts">'+item.ts+'</span><span class="tag tag-'+item.src+'">'+item.src+'</span><span style="color:'+color+';flex:1">'+item.msg+'</span>';
        if (!filterState[item.src] && filterState[item.src] !== undefined) row.style.display = 'none';
        box.appendChild(row);
      });
      while (box.children.length > 200) box.removeChild(box.firstChild);
      if (atBottom && data.logs.length > 0) box.scrollTop = box.scrollHeight;
    } catch(e) {}
  }

  function toggleSrc(src) {
    filterState[src] = document.getElementById('f-'+src).checked;
    document.querySelectorAll('.row-'+src).forEach(r => r.style.display = filterState[src] ? 'flex' : 'none');
  }
  function updateServo(id, val) {
    document.getElementById('val-s'+id).innerText = val+'°';
    fetch('/api/servo/'+id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({angle:parseInt(val)})});
  }
  function momentary(active) {
    fetch('/api/gpio/momentary', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({active})});
  }
  function toggleBlink(active) {
    fetch('/api/gpio/blink', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({active})});
  }
  function clearLogs() { document.getElementById('console').innerHTML = ''; }

  function camOk() {
    const bar=document.getElementById('cam-status-bar'),dot=document.getElementById('cam-dot'),txt=document.getElementById('cam-status-txt');
    bar.classList.remove('cam-err');
    dot.style.background='var(--success)'; dot.style.boxShadow='0 0 8px var(--success)'; dot.style.animation='lp 1.5s infinite';
    txt.innerText='LIVE';
  }
  function camError() {
    const bar=document.getElementById('cam-status-bar'),dot=document.getElementById('cam-dot'),txt=document.getElementById('cam-status-txt');
    bar.classList.add('cam-err');
    dot.style.background='var(--danger)'; dot.style.boxShadow='0 0 8px var(--danger)'; dot.style.animation='none';
    txt.innerText='OFFLINE';
  }
  function reconnectCam() {
    const img = document.getElementById('cam-img');
    img.src = img.src.split('?')[0]+'?t='+Date.now();
  }

  setInterval(refreshStatus, 500);
  setInterval(refreshLogs, 500);
  refreshStatus(); refreshLogs();
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
            if throttled not in ("OK", "Unknown"):
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
                    if uart_dev and len(data) == IBUS_FRAME_LEN and data[:2] == IBUS_HEADER:
                        with suppress(Exception):
                            uart_dev.write(data)
                            with state.lock:
                                state.packets_uart_tx += 1

                    if state.packets_rx % 50 == 0:
                        state.add_log("RC", "Relayed 50 iBUS frames to ESP32")
                    if state.packets_rx == 1:
                        print(f"✓ First raw iBUS packet received from {addr[0]}")
                        state.add_log("RC", f"First UDP frame from {sender}")
            except Exception as e:
                state.add_log("PI", f"UDP receive error: {e}")

        # Read incoming UART logs from ESP32
        if uart_dev:
            with suppress(Exception):
                if available := uart_dev.in_waiting:
                    chunk = uart_dev.read(available)
                    for b in chunk:
                        if b == ord('\n'):
                            if line_str := uart_line_buf.decode('ascii', errors='replace').strip():
                                state.add_log("ESP32", line_str)
                                with state.lock:
                                    state.uart_rx_lines += 1
                            uart_line_buf.clear()
                        elif b != ord('\r'):
                            uart_line_buf.append(b)
                            if len(uart_line_buf) > 256:
                                uart_line_buf.clear()  # Prevent memory leak on missing newlines

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
            bucket = int(now_mono) // 5
            if bucket != last_no_rx_log_sec:
                last_no_rx_log_sec = bucket
                state.add_log("PI", "⚠ RC signal lost – sent NO_SIGNAL to ESP32")

def create_app(state: SharedState, gpio: GpioController, args):
    app = Flask(__name__)

    @app.get("/")
    def dashboard():
        video_url = f"http://{args.listen_ip if args.listen_ip != '0.0.0.0' else '192.168.50.2'}:8090/video_feed"
        return render_template_string(DASHBOARD_HTML, video_url=video_url)


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
                "blink_active": state.blink_active,
                "relay_state": state.relay_state,
            }
        return jsonify(payload)

    @app.get("/api/logs")
    def api_logs():
        try:
            since = int(request.args.get("since", "0"))
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

    # Start System Monitor
    sys_mon = SystemMonitor(state)

    bridge_thread = threading.Thread(target=bridge_loop, args=(state, args), daemon=True)

    bridge_thread.start()
    
    time.sleep(0.5)
    app = create_app(state, gpio, args)

    try:
        app.run(host=args.web_host, port=args.web_port, debug=False, threaded=True, use_reloader=False)
    finally:
        gpio.cleanup()

if __name__ == "__main__":
    main()