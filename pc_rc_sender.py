#!/usr/bin/env python3
"""
Flysky iBUS RC Sender for Underwater Rover

Reads Flysky remote control data from CP2102 TTL converter (iBUS protocol)
and sends it over UDP to Raspberry Pi for autonomous underwater vehicle control.

Usage (auto-detect):
  python3 pc_rc_sender.py --pi-ip pi04b.local

Usage (explicit):
  python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 --pi-ip pi04b.local --pi-port 5000 --hz 50

Data Flow:
  Flysky Remote (2.4GHz)
    → CP2102 TTL Converter (USB)
    → This script (iBUS parsing)
    → UDP frames
    → Raspberry Pi (pi04b.local:5000)
    → Dashboard + UART → ESP32
"""
import argparse
import glob
import os
import socket
import struct
import sys
import time

import serial

# iBUS Protocol Constants (Flysky Remote)
IBUS_FRAME_LEN = 32        # Each iBUS frame is 32 bytes
IBUS_HEADER = b"\x20\x40"  # Frame starts with these two bytes


def auto_detect_serial() -> str:
    """
    Auto-detect CP2102 / CH340 USB serial adapters.

    Scans common Linux serial port patterns and returns the first match.
    Prints a warning if multiple candidates exist (user should specify --serial-port).

    Returns:
        str: Device path (e.g. /dev/ttyUSB0) or empty string if none found
    """
    candidates = (
        sorted(glob.glob("/dev/ttyUSB*")) +
        sorted(glob.glob("/dev/ttyACM*")) +
        sorted(glob.glob("/dev/ttyS[1-9]*"))
    )
    if not candidates:
        return ""
    if len(candidates) > 1:
        print(f"[WARN] Multiple serial devices found: {candidates}")
        print(f"[WARN] Using {candidates[0]} – pass --serial-port to override")
    else:
        print(f"[INFO] Auto-detected serial port: {candidates[0]}")
    return candidates[0]


def parse_ibus_frame(frame: bytes):
    """
    Parse a raw iBUS frame and extract 14 channel values.

    iBUS Frame Format:
    - Byte 0-1:     Header (0x20 0x40)
    - Byte 2-29:    14 channels × 2 bytes each (16-bit little-endian)
    - Byte 30-31:   Checksum (16-bit little-endian)

    Args:
        frame: Raw 32-byte iBUS frame

    Returns:
        list: 14 channel values (1000-2000 μs) or None if invalid
    """
    # Validate frame length
    if len(frame) != IBUS_FRAME_LEN:
        return None
    # Check frame header
    if frame[:2] != IBUS_HEADER:
        return None

    # Validate checksum: 0xFFFF - sum(bytes 0-29) should equal checksum bytes
    expected_checksum = struct.unpack_from("<H", frame, 30)[0]
    checksum = (0xFFFF - (sum(frame[:30]) & 0xFFFF)) & 0xFFFF
    if checksum != expected_checksum:
        return None

    # Extract 14 channel values (2 bytes each, little-endian)
    channels = [struct.unpack_from("<H", frame, 2 + 2 * i)[0] for i in range(14)]
    return channels


def read_ibus_frame(ser: serial.Serial):
    """
    Read and synchronize to an iBUS frame from serial port.

    Scans for the iBUS header (0x20 0x40) then reads remaining 30 bytes.
    Handles partial frames and synchronization loss gracefully.

    Args:
        ser: Open serial.Serial instance (CP2102 device)

    Returns:
        bytes: 32-byte iBUS frame or None on timeout/error
    """
    while True:
        # Look for first header byte (with timeout handled by ser.timeout)
        first = ser.read(1)
        if not first:
            return None          # Timeout – caller re-enters loop
        if first != b"\x20":
            continue

        # Look for second header byte
        second = ser.read(1)
        if not second:
            return None

        # Handle case where we might have sync slipped to the middle of a stream
        # e.g., ... 0x20 0x20 0x40 ...
        # If second byte is 0x20, it might be the START of the new frame, not the 2nd byte.
        if second == b"\x20":
            # Peek next byte to see if it is 0x40
            # Note: pyserial peek isn't standard, so we just read one more.
            third = ser.read(1)
            if not third:
                return None

            if third == b"\x40":
                # Found 0x20 0x20 0x40 -> The second 0x20 was the start.
                # We have consumed header (second, third).
                # Need to read rest.
                rest = ser.read(IBUS_FRAME_LEN - 2)
                if len(rest) != IBUS_FRAME_LEN - 2:
                    return None
                return b"\x20\x40" + rest
            elif third == b"\x20":
                # 0x20 0x20 0x20 ... keep trying to sync
                continue
            else:
                # 0x20 0x20 0x?? -> Not a valid header. Reset.
                continue

        if second != b"\x40":
            continue

        # Found 0x20 0x40
        rest = ser.read(IBUS_FRAME_LEN - 2)
        if len(rest) != IBUS_FRAME_LEN - 2:
            return None

        return first + second + rest


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Read Flysky iBUS from serial and send via UDP to Raspberry Pi"
    )
    parser.add_argument(
        "--serial-port",
        default="",
        help="CP2102 serial device, e.g. /dev/ttyUSB0. Auto-detected if not set.",
    )
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate (default: 115200)")
    parser.add_argument("--pi-ip", required=True, help="Raspberry Pi IP address (e.g. pi04b.local)")
    parser.add_argument("--pi-port", type=int, default=5000, help="Raspberry Pi UDP port (default: 5000)")
    parser.add_argument("--hz", type=float, default=50.0, help="Send rate in Hz (default: 50)")
    parser.add_argument("--print-every", type=int, default=10, help="Print channel values every N sent packets (default: 10)")
    args = parser.parse_args()

    # Resolve serial port – auto-detect if not specified
    serial_port = args.serial_port.strip()
    if not serial_port:
        serial_port = auto_detect_serial()
    if not serial_port:
        print("[FAIL] No serial port found. Plug in CP2102 adapter or pass --serial-port.")
        sys.exit(1)

    # Validate port exists before starting loop
    if not os.path.exists(serial_port):
        print(f"[FAIL] Serial device does not exist: {serial_port}")
        print("       Check USB connection and driver (lsusb, dmesg | tail -20)")
        sys.exit(1)

    # Calculate send interval (1/Hz)
    interval = 1.0 / max(args.hz, 1.0)
    next_send = 0.0
    sent_count = 0
    fail_count = 0

    # Create UDP socket for sending to Pi
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(f"\n{'='*55}")
    print(f"  RC SENDER - Flysky iBUS → UDP → Raspberry Pi")
    print(f"{'='*55}")
    print(f"  Serial port : {serial_port} @ {args.baud} baud")
    print(f"  Destination : {args.pi_ip}:{args.pi_port}  (UDP)")
    print(f"  Send rate   : {args.hz:.1f} Hz")
    print(f"{'='*55}")
    print("  Move Flysky sticks to start sending data...")
    print("  Press Ctrl+C to exit.\n")

    try:
        while True:
            try:
                # Open serial connection to CP2102/Flysky receiver
                # timeout=0.1 lets read_ibus_frame return None on timeout instead of blocking
                with serial.Serial(serial_port, args.baud, timeout=0.1, exclusive=True) as ser:
                    print(f"[OK]  Serial port {serial_port} opened.")
                    fail_count = 0

                    while True:
                        # Read one complete iBUS frame
                        frame = read_ibus_frame(ser)
                        if not frame:
                            continue

                        # Parse frame to extract 14 channel values
                        channels = parse_ibus_frame(frame)
                        if channels is None:
                            continue

                        # Apply frame rate limiting (send at ~Hz)
                        now = time.monotonic()
                        if now < next_send:
                            continue

                        # Send raw 32-byte iBUS frame to Pi
                        sock.sendto(frame, (args.pi_ip, args.pi_port))

                        sent_count += 1
                        if args.print_every > 0 and sent_count % args.print_every == 0:
                            ch_str = " ".join(str(ch) for ch in channels)
                            print(f"TX #{sent_count}: Raw iBUS Frame sent (channels: {ch_str})")

                        next_send = now + interval

            except KeyboardInterrupt:
                # Must be before Exception so Ctrl+C is caught here first
                raise

            except serial.SerialException as e:
                fail_count += 1
                print(f"[WARN] Serial error ({fail_count}): {e}. Reconnecting in 2s...")
                time.sleep(2)

            except Exception as e:
                fail_count += 1
                print(f"[WARN] Unexpected error ({fail_count}): {e}. Retrying in 2s...")
                time.sleep(2)

    except KeyboardInterrupt:
        print("\n[INFO] Exiting – RC sender stopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()