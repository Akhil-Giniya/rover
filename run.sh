#!/usr/bin/env bash
# =============================================================================
# run.sh  —  Automated Rover Flow (Local + SSH)
#
# This script automates the manual flow defined in run.md:
# 1. Launches RC sender locally.
# 2. SSH into Pi -> Launches Rover System.
# 3. SSH into Pi -> Launches Video Stream.
#
# USAGE:
#   chmod +x run.sh && ./run.sh
# =============================================================================

# ── Config (From run.md) ──────────────────────────────────────────────────────
PI_IP="192.168.50.2"
PI_USER="pi04b"
PI_PASS="123456"
PI_DIR="Documents/rover"
RC_PORT=5000
RC_HZ=50
DASHBOARD_PORT=8080
VIDEO_PORT=8081

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
step() { echo -e "\n${BOLD}━━  $*  ━━${NC}"; }

# ── Dependencies ──────────────────────────────────────────────────────────────
step "Phase 0: Checking Dependencies"

for cmd in sshpass gnome-terminal python3; do
    if ! command -v "$cmd" &>/dev/null; then
        fail "Required command '$cmd' not found. Please install it."
    fi
done
ok "All dependencies found."

# ── Cleanup ───────────────────────────────────────────────────────────────────
cleanup() {
    echo ""
    step "Shutting down and cleaning up..."
    
    # Local
    info "Killing local RC sender..."
    pkill -f pc_rc_sender.py 2>/dev/null || true
    
    # Remote
    info "Killing remote rover processes on Pi..."
    sshpass -p "${PI_PASS}" ssh -o StrictHostKeyChecking=no "${PI_USER}@${PI_IP}" \
        "pkill -f pi_rover_system; pkill -f pi_web_video_stream; pkill -f rpicam_stream" 2>/dev/null || true
    
    ok "Cleanup complete. Goodbye!"
}

# Trap Ctrl+C and script exit
trap cleanup EXIT INT TERM

# ── Initialize ────────────────────────────────────────────────────────────────
step "Phase 1: Initial Cleanup"
info "Ensuring a clean slate before starting..."
# Run an explicit cleanup before starting to clear old sockets/processes
sshpass -p "${PI_PASS}" ssh -o StrictHostKeyChecking=no "${PI_USER}@${PI_IP}" \
    "pkill -f pi_rover_system; pkill -f pi_web_video_stream; pkill -f rpicam_stream" 2>/dev/null || true
pkill -f pc_rc_sender.py 2>/dev/null || true

# ── Launch Services ───────────────────────────────────────────────────────────
step "Phase 2: Launching Services"

# 1. Local RC Sender
info "Launching RC Sender locally..."
gnome-terminal --title="RC Sender (Local)" -- bash -c "
    echo '=== RC Sender (Local) ==='
    python3 pc_rc_sender.py --serial-port /dev/ttyUSB0 --pi-ip ${PI_IP} --pi-port ${RC_PORT} --hz ${RC_HZ}
    echo -e '\nSender exited. Press Enter to close.'
    read
" &

# 2. Pi Rover System
info "Launching Rover System on Pi (${PI_IP})..."
gnome-terminal --title="Pi Rover System" -- bash -c "
    echo '=== Pi Rover System ==='
    sshpass -p '${PI_PASS}' ssh -t -o StrictHostKeyChecking=no ${PI_USER}@${PI_IP} \
        'cd ${PI_DIR} && python3 pi_rover_system.py --listen-ip 0.0.0.0 --listen-port ${RC_PORT} --uart-port /dev/serial0 --baud 115200 --web-host 0.0.0.0 --web-port ${DASHBOARD_PORT}'
    echo -e '\nRover System exited. Press Enter to close.'
    read
" &

# 3. Pi Video Stream
info "Launching Video Stream on Pi (${PI_IP})..."
gnome-terminal --title="Pi Video Stream" -- bash -c "
    echo '=== Pi Video Stream ==='
    sshpass -p '${PI_PASS}' ssh -t -o StrictHostKeyChecking=no ${PI_USER}@${PI_IP} \
        'cd ${PI_DIR} && python3 pi_web_video_stream.py --host 0.0.0.0 --port ${VIDEO_PORT}'
    echo -e '\nVideo Stream exited. Press Enter to close.'
    read
" &

# ── Status ────────────────────────────────────────────────────────────────────
step "All Systems Active"
ok "Dashboard: http://${PI_IP}:${DASHBOARD_PORT}"
ok "Video Feed: http://${PI_IP}:${VIDEO_PORT}"
info "Keep THIS terminal window open to maintain the cleanup trap."
info "Press Ctrl+C here to stop ALL services."

# Keep the script running to maintain the cleanup trap
wait
