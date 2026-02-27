#!/bin/bash

PI_USER="pi04b"
PI_IP="192.168.50.2"
PI_PASS="123456"
PI_DIR="Documents/rover"
RC_PORT=5000
RC_HZ=50
DASHBOARD_PORT=8080
VIDEO_PORT=8081

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=6"

echo "Starting Rover System..."

###############################
# 1️⃣ Start PC RC Sender
###############################
gnome-terminal -- bash -c "
echo 'Starting pc_rc_sender.py...'
python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip $PI_IP \
  --pi-port $RC_PORT \
  --hz $RC_HZ
echo -e '\nSender exited. Press Enter to close.'
read
" &

sleep 2

###############################
# 2️⃣ Start Pi Rover System (auto-retry if needed)
###############################
gnome-terminal -- bash -c "
echo 'Starting pi_rover_system.py on Pi...'
sshpass -p '$PI_PASS' ssh -t $SSH_OPTS $PI_USER@$PI_IP \
    'cd $PI_DIR && python3 pi_rover_system.py \
      --listen-ip 0.0.0.0 \
      --listen-port $RC_PORT \
      --uart-port /dev/serial0 \
      --baud 115200 \
      --web-host 0.0.0.0 \
      --web-port $DASHBOARD_PORT || {
        echo Error detected. Killing process and retrying...
        pkill -f pi_rover_system
        sleep 2
        python3 pi_rover_system.py \
          --listen-ip 0.0.0.0 \
          --listen-port $RC_PORT \
          --uart-port /dev/serial0 \
          --baud 115200 \
          --web-host 0.0.0.0 \
          --web-port $DASHBOARD_PORT
      }'
echo -e '\nRover System exited. Press Enter to close.'
read
" &

sleep 2

###############################
# 3️⃣ Start Pi Video Stream (auto-retry if needed)
###############################
gnome-terminal -- bash -c "
echo 'Starting pi_web_video_stream.py on Pi...'
sshpass -p '$PI_PASS' ssh -t $SSH_OPTS $PI_USER@$PI_IP \
    'cd $PI_DIR && python3 pi_web_video_stream.py \
      --host 0.0.0.0 \
      --port $VIDEO_PORT || {
        echo Error detected. Killing stream processes and retrying...
        pkill -f pi_web_video_stream
        pkill -f rpicam_stream
        sleep 2
        python3 pi_web_video_stream.py \
          --host 0.0.0.0 \
          --port $VIDEO_PORT
      }'
echo -e '\nVideo Stream exited. Press Enter to close.'
read
" &

sleep 3

###############################
# 4️⃣ Open Browser
###############################
echo "Opening web interface..."
xdg-open http://$PI_IP:$DASHBOARD_PORT

echo "All systems launched 🚀"