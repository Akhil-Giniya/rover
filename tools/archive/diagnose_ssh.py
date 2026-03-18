#!/usr/bin/env python3
"""
Minimal Pi diagnostic via paramiko (SSH library)
Doesn't require sshpass or interactive prompts
"""
import sys
import subprocess

# First try: install paramiko if not present
try:
    import paramiko
except ImportError:
    print("Installing paramiko for SSH...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "paramiko"], check=False)
    import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

PI_IP = "192.168.50.2"
PI_USER = "pi04b"
PI_PASS = "123456"

print(f"Connecting to {PI_IP}...")
try:
    ssh.connect(PI_IP, username=PI_USER, password=PI_PASS, timeout=5, allow_agent=False, look_for_keys=False)
    print("✓ Connected")
except Exception as e:
    print(f"✗ Connection failed: {e}")
    sys.exit(1)

commands = [
    ("Check process", "ps aux | grep -E 'pi_rover|python3' | grep -v grep | head -3"),
    ("Check UDP 5000", "netstat -tuln | grep 5000 || echo 'Not listening'"),
    ("Check UART device", "ls -la /dev/serial0 2>/dev/null || echo 'Not found'"),
    ("Check Ethernet", "ip -brief addr show eth0 || echo 'Not found'"),
    ("Check recent logs", "tail -20 /tmp/pi_rover.log 2>/dev/null || echo 'No log file'"),
]

print("\n=== Remote Diagnostics ===\n")
for label, cmd in commands:
    print(f"[*] {label}:")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    output = stdout.read().decode("utf-8", errors="replace").strip()
    if output:
        for line in output.split("\n")[:5]:  # Limit to 5 lines per command
            print(f"    {line}")
    else:
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if err:
            print(f"    {err}")

ssh.close()
print("\n=== End Diagnostics ===")
