#!/usr/bin/env python3
import argparse
import select
import socket
import time

import serial


def main():
    parser = argparse.ArgumentParser(description="Receive RC over UDP and forward directly to UART")
    parser.add_argument("--listen-ip", default="0.0.0.0", help="IP to bind UDP socket (default: 0.0.0.0)")
    parser.add_argument("--listen-port", type=int, default=5000, help="UDP port to listen on (default: 5000)")
    parser.add_argument("--uart-port", default="/dev/serial0", help="UART device on Pi (default: /dev/serial0)")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    parser.add_argument("--failsafe-timeout", type=float, default=1.0, help="Seconds before NO_SIGNAL (default: 1.0)")
    parser.add_argument("--print-every", type=int, default=10, help="Print every N forwarded packets (default: 10)")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.listen_ip, args.listen_port))
    sock.setblocking(False)

    last_rx_time = 0.0
    failsafe_sent = False
    rx_count = 0

    print(f"Listening UDP on {args.listen_ip}:{args.listen_port}")
    print(f"Forwarding to UART {args.uart_port} @ {args.baud}")

    with serial.Serial(args.uart_port, args.baud, timeout=0) as uart:
        while True:
            readable, _, _ = select.select([sock], [], [], 0.05)
            if readable:
                data, addr = sock.recvfrom(2048)
                line = data.strip()
                if line:
                    uart.write(line + b"\n")
                    last_rx_time = time.monotonic()
                    failsafe_sent = False
                    rx_count += 1

                    if args.print_every > 0 and rx_count % args.print_every == 0:
                        print(f"RX {addr[0]}:{addr[1]} -> {line.decode('ascii', errors='replace')}")

            if last_rx_time > 0 and (time.monotonic() - last_rx_time) >= args.failsafe_timeout:
                if not failsafe_sent:
                    uart.write(b"NO_SIGNAL\n")
                    print("FAILSAFE: NO_SIGNAL")
                    failsafe_sent = True


if __name__ == "__main__":
    main()