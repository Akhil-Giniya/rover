#!/usr/bin/env python3
import argparse
import os
import socket
import subprocess
import sys

try:
    import serial
except Exception:
    serial = None

try:
    import cv2
except Exception:
    cv2 = None


def ok(msg):
    print(f"[OK] {msg}")


def warn(msg):
    print(f"[WARN] {msg}")


def fail(msg):
    print(f"[FAIL] {msg}")


def run_cmd(cmd):
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def check_eth(interface):
    r = run_cmd(["ip", "-brief", "link", "show", interface])
    if r.returncode != 0:
        fail(f"Ethernet interface {interface} missing")
        return False
    if " UP " in f" {r.stdout.strip()} ":
        ok(f"Ethernet {interface} is UP")
        return True
    warn(f"Ethernet {interface} exists but is DOWN")
    return False


def check_no_wifi_bluetooth():
    try:
        rf = run_cmd(["rfkill", "list"])
        if rf.returncode != 0:
            warn("rfkill not available; cannot verify Wi-Fi/Bluetooth state")
            return
    except FileNotFoundError:
        warn("rfkill not installed; cannot verify Wi-Fi/Bluetooth state")
        return
    text = rf.stdout.lower()
    wifi_blocked = "wireless" in text and "soft blocked: yes" in text
    bt_blocked = "bluetooth" in text and text.count("soft blocked: yes") >= 1
    if wifi_blocked:
        ok("Wi-Fi appears blocked")
    else:
        warn("Wi-Fi may still be enabled")
    if bt_blocked:
        ok("Bluetooth appears blocked")
    else:
        warn("Bluetooth may still be enabled")


def check_uart(uart_port, baud):
    if not os.path.exists(uart_port):
        fail(f"UART device {uart_port} not found")
        return False
    if serial is None:
        warn("pyserial not installed; skipping UART open test")
        return False
    try:
        with serial.Serial(uart_port, baud, timeout=0.2) as s:
            s.write(b"PI_UART_TEST\n")
        ok(f"UART open/write successful on {uart_port}")
        return True
    except Exception as exc:
        fail(f"UART test failed: {exc}")
        return False


def check_camera(index):
    if cv2 is None:
        warn("opencv-python not installed; skipping camera check")
        return False
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        warn(f"Camera index {index} not available")
        return False
    ok_ret, _ = cap.read()
    cap.release()
    if ok_ret:
        ok(f"Camera index {index} read successful")
        return True
    warn(f"Camera index {index} opened but frame read failed")
    return False


def check_udp_bind(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
        ok(f"UDP bind on 0.0.0.0:{port} successful")
        return True
    except Exception as exc:
        fail(f"UDP bind failed on port {port}: {exc}")
        return False
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="Physical/system check for Pi rover stack")
    parser.add_argument("--eth-interface", default="eth0")
    parser.add_argument("--uart-port", default="/dev/serial0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--udp-port", type=int, default=5000)
    args = parser.parse_args()

    print("=== Raspberry Pi Rover Hardware Check ===")
    eth_ok = check_eth(args.eth_interface)
    check_no_wifi_bluetooth()
    uart_ok = check_uart(args.uart_port, args.baud)
    cam_ok = check_camera(args.camera_index)
    udp_ok = check_udp_bind(args.udp_port)

    print("=== Summary ===")
    print(f"Ethernet: {'OK' if eth_ok else 'NOT OK'}")
    print(f"UART: {'OK' if uart_ok else 'NOT OK'}")
    print(f"Camera: {'OK' if cam_ok else 'NOT OK'}")
    print(f"UDP Port: {'OK' if udp_ok else 'NOT OK'}")

    if not (eth_ok and uart_ok and udp_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()