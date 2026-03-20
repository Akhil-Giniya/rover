# Rover System — Quick Run Guide

## PC-Side Ethernet Setup (one-time)

Set your PC's Ethernet adapter to a static IP:
- IP: `192.168.50.1`
- Netmask: `255.255.255.0` (or /24)
- Gateway: leave blank

On Linux:
```bash
sudo ip addr add 192.168.50.1/24 dev eth0
sudo ip link set eth0 up
```

## Pi-Side Setup (one-time)

SSH into Pi and run the Ethernet-only setup:
```bash
ssh pi04b@192.168.50.2    # password: 123456
cd rover
sudo bash ethernet_only_setup.sh
sudo reboot
```


### Option A: Manual launch

**Terminal 1 — RC Sender (PC)**
```bash
cd rover
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip 192.168.50.2 \
  --pi-port 5000 \
  --hz 50
```

**Terminal 2 — Rover System (Pi)**
```bash
ssh pi04b@192.168.50.2    # password: 123456
cd rover
python3 pi_rover_system.py \
  --listen-ip 0.0.0.0 \
  --listen-port 5000 \
  --uart-port /dev/serial0 \
  --baud 115200 \
  --web-host 0.0.0.0 \
  --web-port 8080
```

**Terminal 3 — Camera Feed (Pi)**
```bash
ssh pi04b@192.168.50.2    # password: 123456
cd rover
python3 pi_web_video_stream.py \
  --host 0.0.0.0 \
  --port 8081
```

## Dashboard

Open browser: **http://192.168.50.2:8080**

> Works fully offline — no internet/WiFi needed, just Ethernet cable.

## Troubleshooting

```bash
# Kill stuck processes on Pi
pkill -f pi_rover_system
pkill -f pi_web_video_stream
pkill rpicam_stream
```
