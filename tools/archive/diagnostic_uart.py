#!/usr/bin/env python3
"""
Comprehensive UART diagnostic for rover system.
Run this on the Pi to verify RC data is flowing to ESP32.
"""

import subprocess
import os
import sys
import time

def run_cmd(cmd):
    """Run shell command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

print("=" * 70)
print("ROVER UART DIAGNOSTIC")
print("=" * 70)
print()

# Check 1: Is /dev/serial0 free?
print("[1] UART Device Status")
print("-" * 70)
lsof_result = run_cmd("lsof /dev/serial0 2>/dev/null")
if lsof_result:
    print("❌ /dev/serial0 is in use by:")
    for line in lsof_result.split('\n')[:3]:
        print(f"   {line}")
else:
    print("✅ /dev/serial0 is FREE (agetty disabled)")
print()

# Check 2: Is pi_rover_system running?
print("[2] Pi Rover Service Status")
print("-" * 70)
ps_result = run_cmd("ps aux | grep pi_rover_system | grep -v grep")
if ps_result:
    pid = ps_result.split()[1]
    print(f"✅ Service running (PID {pid})")
else:
    print("❌ Service NOT running")
print()

# Check 3: Is UDP port 5000 listening?
print("[3] Network Port Status")
print("-" * 70)
netstat = run_cmd("netstat -lnp 2>/dev/null | grep 5000 || ss -lnp 2>/dev/null | grep 5000")
if "5000" in netstat:
    print("✅ UDP 5000 listening")
else:
    print("❌ UDP 5000 not listening")

netstat8080 = run_cmd("netstat -lnp 2>/dev/null | grep 8080 || ss -lnp 2>/dev/null | grep 8080")
if "8080" in netstat8080:
    print("✅ TCP 8080 (web) listening")
else:
    print("❌ TCP 8080 not listening")
print()

# Check 4: Test UART write
print("[4] UART Write Test")
print("-" * 70)
try:
    import serial
    ser = serial.Serial('/dev/serial0', 115200, timeout=0.1)
    test_data = b"TEST_DIAG_12345\n"
    n = ser.write(test_data)
    ser.close()
    print(f"✅ Successfully wrote {n} bytes to /dev/serial0")
except Exception as e:
    print(f"❌ Failed to write: {e}")
print()

# Check 5: Monitor UART for 3 seconds
print("[5] UART Data Flow Test (3 second monitor)")
print("-" * 70)
monitor_result = run_cmd("timeout 3 dd if=/dev/serial0 2>/dev/null | wc -c")
try:
    bytes_count = int(monitor_result)
    if bytes_count > 0:
        print(f"✅ DETECTED {bytes_count} bytes flowing on UART")
        print("   ✓ RC signals ARE reaching ESP32")
    else:
        print(f"❌ NO data on UART (0 bytes)")
        print("   Service may not be writing, or no ESP32 connected")
except:
    print(f"? Could not determine: {monitor_result}")
print()

# Check 6: Dashboard API status
print("[6] Dashboard API Status")
print("-" * 70)
api_test = run_cmd("timeout 2 curl -s http://localhost:8080/api/status 2>/dev/null")
if "{" in api_test and "packets_rx" in api_test:
    print("✅ Dashboard responsive")
    import json
    try:
        data = json.loads(api_test)
        print(f"   Packets RX: {data.get('packets_rx')}")
        print(f"   Packets UART TX: {data.get('packets_uart_tx')}")
        print(f"   ESP32 RX lines: {data.get('uart_rx_lines')}")
    except:
        pass
else:
    print("❌ Dashboard not responding")
print()

print("=" * 70)
print("END DIAGNOSTIC")
print("=" * 70)
