#!/bin/bash

PI_USER="pi04b"
PI_IP="192.168.50.2"
PI_PASS="123456"

LOCAL_DIR="$HOME/Documents/rover"

echo "Starting Rover System..."

###############################
# 1️⃣ Start PC RC Sender
###############################
gnome-terminal -- bash -c "
cd $LOCAL_DIR || exit
echo 'Starting pc_rc_sender.py...'
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip $PI_IP \
  --pi-port 5000 \
  --hz 50
exec bash
"

sleep 2

###############################
# 2️⃣ Start Pi Rover System (auto-retry if needed)
###############################
gnome-terminal -- bash -c "
echo 'Starting pi_rover_system.py on Pi...'
sshpass -p '$PI_PASS' ssh -tt $PI_USER@$PI_IP << EOF
cd Documents/rover || exit

echo 'Running pi_rover_system.py...'
python3 pi_rover_system.py \
  --listen-ip 0.0.0.0 \
  --listen-port 5000 \
  --uart-port /dev/serial0 \
  --baud 115200 \
  --web-host 0.0.0.0 \
  --web-port 8080 || {

    echo 'Error detected. Killing process and retrying...'
    pkill -f pi_rover_system
    sleep 2

    python3 pi_rover_system.py \
      --listen-ip 0.0.0.0 \
      --listen-port 5000 \
      --uart-port /dev/serial0 \
      --baud 115200 \
      --web-host 0.0.0.0 \
      --web-port 8080
}
EOF
exec bash
"

sleep 2

###############################
# 3️⃣ Start Pi Video Stream (auto-retry if needed)
###############################
gnome-terminal -- bash -c "
echo 'Starting pi_web_video_stream.py on Pi...'
sshpass -p '$PI_PASS' ssh -tt $PI_USER@$PI_IP << EOF
cd Documents/rover || exit

echo 'Running pi_web_video_stream.py...'
python3 pi_web_video_stream.py \
  --host 0.0.0.0 \
  --port 8081 || {

    echo 'Error detected. Killing stream processes and retrying...'
    pkill -f pi_web_video_stream
    pkill rpicam_stream
    sleep 2

    python3 pi_web_video_stream.py \
      --host 0.0.0.0 \
      --port 8081
}
EOF
exec bash
"

sleep 3

###############################
# 4️⃣ Open Browser
###############################
echo "Opening web interface..."
xdg-open http://$PI_IP:8080

echo "All systems launched 🚀"