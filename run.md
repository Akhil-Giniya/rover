new terminal

cd Documents/rover

python3 pc_rc_sender.py \
  --serial-port /dev/ttyUSB0 \
  --pi-ip 192.168.50.2 \
  --pi-port 5000 \
  --hz 50

new terminal

ssh pi04b@192.168.50.2
password: 123456

cd Documents/rover

python3 pi_rover_system.py \
  --listen-ip 0.0.0.0 \
  --listen-port 5000 \
  --uart-port /dev/serial0 \
  --baud 115200 \
  --web-host 0.0.0.0 \
  --web-port 8080

if you get error 

pkill -f pi_rover_system

then run again

new terminal

ssh pi04b@192.168.50.2
password: 123456

cd Documents/rover

python3 pi_web_video_stream.py \
  --host 0.0.0.0 \
  --port 8081

if you get error

pkill -f pi_web_video_stream
pkill rpicam_stream

then run again

all running 

open browser and go to http://192.168.50.2:8080



