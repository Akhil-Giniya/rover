#!/usr/bin/env bash
# =============================================================================
# launch.sh  —  Simple Rover Launcher
#
# STEP 1 : Detect hardware  (Raspberry Pi ping + RC receiver USB scan)
# STEP 2 : Set USB port permissions for RC receiver
# STEP 3 : SSH into Pi → launch camera feed  (pi_web_video_stream.py)
# STEP 4 : SSH into Pi → launch rover system (pi_rover_system.py)
# STEP 5 : Launch RC sender on this PC       (pc_rc_sender.py)
#
# USAGE:
#   chmod +x launch.sh && ./launch.sh
# =============================================================================

# ── Config ────────────────────────────────────────────────────────────────────
PI_IP="192.168.50.2"
PI_USER="pi04b"
PI_PASS="123456"
PI_PORT=5000
RC_HZ=50

# ── Pi Commands ───────────────────────────────────────────────────────────────
PI_DIR="~/rover"
PI_PYTHON="${PI_DIR}/.venv/bin/python3"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
step() { echo -e "\n${BOLD}━━  $*  ━━${NC}"; }

SSH_OPTS="-o StrictHostKeyChecking=no -o PasswordAuthentication=yes \
          -o PubkeyAuthentication=no -o LogLevel=ERROR \
          -o ConnectTimeout=6 -o ServerAliveInterval=10"

# =============================================================================
# STEP 0 — Check PC Dependencies
# =============================================================================
step "STEP 0 — Checking PC Dependencies"

for cmd in sshpass gnome-terminal python3 ping; do
    if ! command -v "$cmd" &>/dev/null; then
        fail "Required command '$cmd' not found. Please install it."
    fi
done
ok "All local dependencies found"

# =============================================================================
# STEP 1 — Detect Hardware
# =============================================================================
step "STEP 1 — Detecting Hardware"

# ── Raspberry Pi ──────────────────────────────────────────────────────────────
info "Pinging Raspberry Pi at ${PI_IP} ..."
if ping -c 2 -W 2 "${PI_IP}" &>/dev/null; then
    ok "Raspberry Pi reachable at ${PI_IP}"
else
    fail "Raspberry Pi NOT reachable at ${PI_IP}. Check your Ethernet cable / IP."
fi

# ── RC Receiver (USB) ─────────────────────────────────────────────────────────
info "Scanning for RC receiver USB port ..."
RC_PORT=""
for port in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2 /dev/ttyACM0 /dev/ttyACM1; do
    [[ -e "$port" ]] && RC_PORT="$port" && break
done

if [[ -n "$RC_PORT" ]]; then
    ok "RC receiver detected at ${RC_PORT}"
else
    warn "No USB RC receiver found — defaulting to /dev/ttyUSB0"
    RC_PORT="/dev/ttyUSB0"
fi

# =============================================================================
# STEP 2 — Set USB Port Permissions
# =============================================================================
step "STEP 2 — Setting USB Port Permissions"

if [[ -e "$RC_PORT" ]]; then
    info "Setting permissions on ${RC_PORT} ..."
    sudo chmod 666 "${RC_PORT}" && ok "${RC_PORT} ready" || warn "chmod failed — may need to run with sudo"
else
    warn "${RC_PORT} not present yet — skipping chmod"
fi

# =============================================================================
# STEP 3 — SSH into Pi: Launch Camera Feed (new terminal window)
# =============================================================================
step "STEP 3 — Launching Camera Feed on Pi"

info "Opening new terminal -> SSH -> pi_web_video_stream.py ..."
gnome-terminal --title="Pi Camera Feed" -- bash -c "
    echo '=== Pi Camera Feed ===';
    sshpass -p '${PI_PASS}' ssh ${SSH_OPTS} ${PI_USER}@${PI_IP} \
        'cd ${PI_DIR} && ${PI_PYTHON} pi_web_video_stream.py';
    echo 'Camera exited. Press Enter to close.';
    read
" &
ok "Camera terminal opened  ->  stream will appear at http://${PI_IP}:8090"

sleep 1

# =============================================================================
# STEP 4 — SSH into Pi: Launch Rover System (new terminal window)
# =============================================================================
step "STEP 4 — Launching Pi Rover System"

info "Opening new terminal -> SSH -> pi_rover_system.py ..."
gnome-terminal --title="Pi Rover System" -- bash -c "
    echo '=== Pi Rover System ===';
    sshpass -p '${PI_PASS}' ssh ${SSH_OPTS} ${PI_USER}@${PI_IP} \
        'cd ${PI_DIR} && ${PI_PYTHON} pi_rover_system.py';
    echo 'Rover system exited. Press Enter to close.';
    read
" &
ok "Rover system terminal opened"

sleep 2

# =============================================================================
# STEP 5 — Launch RC Sender on This PC (foreground)
# =============================================================================
step "STEP 5 — Launching RC Sender on PC"

info "Serial port : ${RC_PORT}"
info "Pi target   : ${PI_IP}:${PI_PORT}"
info "Rate        : ${RC_HZ} Hz"
info "Press Ctrl+C to stop the RC link."
echo ""

# Ensure no previous sender is hogging the serial port
pkill -f pc_rc_sender.py 2>/dev/null || true

cd "$(dirname "$0")" && python3 pc_rc_sender.py \
    --serial-port "${RC_PORT}" \
    --pi-ip       "${PI_IP}" \
    --pi-port     "${PI_PORT}" \
    --hz          "${RC_HZ}"


