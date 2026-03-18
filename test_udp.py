#!/usr/bin/env python3
"""
Standalone UDP Test - Run this FIRST before running pi_rover_system
Helps verify that PC-to-Pi network connectivity is working
"""
import argparse
import socket
import sys
import time

def listen_for_udp(host, port, duration_sec=10):
    """Listen on UDP and print any received packets"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        sock.bind((host, port))
        sock.settimeout(1.0)
        print(f"✓ UDP listener started on {host}:{port}")
        print(f"✓ Waiting for {duration_sec} seconds...")
        print("")
        
        start = time.time()
        packet_count = 0
        
        while time.time() - start < duration_sec:
            try:
                data, addr = sock.recvfrom(2048)
                packet_count += 1
                text = data.decode("ascii", errors="replace").strip()
                print(f"[{time.strftime('%H:%M:%S')}] Packet #{packet_count} from {addr[0]}:{addr[1]}")
                print(f"  → {text[:100]}")
            except socket.timeout:
                pass
        
        if packet_count == 0:
            print("")
            print("✗ NO PACKETS RECEIVED")
            print("")
            print("This means:")
            print("  1. PC sender is NOT running")
            print("  2. PC is sending to the WRONG IP address")
            print("  3. Firewall is blocking UDP on port {port}")
            print("")
            return False
        else:
            print("")
            print(f"✓ Received {packet_count} packets!")
            return True
    except OSError as e:
        print(f"✗ Cannot bind to {host}:{port}: {e}")
        print("  This usually means the port is already in use")
        return False
    finally:
        sock.close()

def main():
    parser = argparse.ArgumentParser(description="UDP Connectivity Test")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="UDP port (default: 5000)")
    parser.add_argument("--duration", type=int, default=10, help="Listen duration in seconds (default: 10)")
    args = parser.parse_args()
    
    print("="*60)
    print("UDP CONNECTIVITY TEST")
    print("="*60)
    print("")
    print("INSTRUCTIONS:")
    print("  1. Start this listener on the Pi/receiving machine")
    print("  2. From PC, run your RC sender pointing to THIS machine's IP:")
    print("     python3 pc_rc_sender.py \\")
    print("       --serial-port /dev/ttyUSB0 \\")
    print(f"       --pi-ip <THIS_MACHINE_IP> \\")
    print(f"       --pi-port {args.port}")
    print("")
    print("="*60)
    print("")
    
    success = listen_for_udp(args.host, args.port, args.duration)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
