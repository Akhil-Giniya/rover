#!/usr/bin/env python3
"""
Diagnostic script to check:
1. Is UDP 5000 receiving packets?
2. Is pi_rover_system running?
3. Are we getting any RC data in the logs?
"""
import socket
import subprocess
import sys
import time

print("=== RC Link Diagnostic ===\n")

# Check if process is running
print("[1] Checking if pi_rover_system is running...")
result = subprocess.run(
    ["pgrep", "-af", "pi_rover_system"],
    capture_output=True,
    text=True
)
if result.returncode == 0:
    print(f"✓ Process running: {result.stdout.strip()}")
else:
    print("✗ Process NOT running - Start it with: python3 pi_rover_system.py")

# Check UDP 5000 for incoming packets
print("\n[2] Listening on UDP 5000 for 3 seconds...")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind(("0.0.0.0", 5000))
    sock.settimeout(3.0)
    print("Waiting for RC packets...")
    try:
        data, addr = sock.recvfrom(2048)
        print(f"✓ Received from {addr}: {data.decode('ascii', errors='replace')[:100]}")
    except socket.timeout:
        print("✗ No packets received on UDP 5000 within 3 seconds!")
        print("  → Is PC sender running?")
        print("  → Is PC sending to the correct Pi IP?")
except Exception as e:
    print(f"✗ UDP bind failed: {e}")
finally:
    sock.close()

# Check netstat for listening ports
print("\n[3] Checking open ports...")
result = subprocess.run(
    ["netstat", "-tuln"],
    capture_output=True,
    text=True
)
for line in result.stdout.split("\n"):
    if "5000" in line or "8080" in line:
        print(f"  {line.strip()}")

print("\n[4] Recent logs from journalctl (if systemd)...")
result = subprocess.run(
    ["journalctl", "-u", "pi_rover_system", "-n", "10", "--no-pager"],
    capture_output=True,
    text=True
)
if result.returncode == 0:
    print(result.stdout[:500])
else:
    print("  (no systemd service found)")

print("\n=== End Diagnostic ===")
