#!/usr/bin/env bash
# =============================================================================
# launch.sh  –  One-Click Rover Launcher (PC → SSH → Pi)
#
# RUN THIS ON YOUR PC. It will:
#   1. Sync project files to Raspberry Pi
#   2. Start pi_rover_system.py on the Pi (over SSH)
#   3. Start pc_rc_sender.py on this PC (reads Flysky iBUS, sends UDP)
#   4. Print the dashboard URL
#   5. Ctrl+C stops everything cleanly
#
# USAGE:
#   chmod +x launch.sh
#   ./launch.sh
#
# REQUIREMENTS (PC):
#   sudo apt install sshpass rsync   (or: pip3 install pyserial)
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
PI_USER="pi04b"
PI_HOST="192.168.50.2"
PI_PASS="123456"
PI_PORT_UDP=5000
PI_PORT_WEB=8080
PI_PORT_VIDEO=8090
PI_ROVER_DIR="/home/pi04b/rover"
UART_PORT="/dev/serial0"
BAUD=115200
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EFFECTIVE_UART_PORT="${UART_PORT}"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
step()  { echo -e "\n${BOLD}▶ $*${NC}"; }

# ── SSH / SCP helpers (non-interactive with sshpass) ─────────────────────────
SSH_OPTS="-o StrictHostKeyChecking=no -o PubkeyAuthentication=no -o PasswordAuthentication=yes -o LogLevel=ERROR"
SSH="sshpass -p ${PI_PASS} ssh ${SSH_OPTS}"
SCP="sshpass -p ${PI_PASS} scp ${SSH_OPTS}"

find_flysky_port() {
  local candidates=()

  if [[ -d /dev/serial/by-id ]]; then
    while IFS= read -r dev; do
      candidates+=("$dev")
    done < <(find /dev/serial/by-id -maxdepth 1 -type l 2>/dev/null | sort)
  fi

  for pat in /dev/ttyUSB* /dev/ttyACM* /dev/ttyS[1-9]*; do
    [[ -e "$pat" ]] && candidates+=("$pat")
  done

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo ""
    return
  fi

  echo "${candidates[0]}"
}

pi_run() {
  # Run a command on the Pi and return its output
  $SSH ${PI_USER}@${PI_HOST} "$@"
}

detect_pi_uart_port() {
  local requested="$1"

  if pi_run "test -e ${requested}" >/dev/null 2>&1; then
    echo "${requested}"
    return 0
  fi

  local fallback
  fallback="$(pi_run "for d in /dev/serial0 /dev/ttyAMA0 /dev/ttyS0; do [ -e \"\$d\" ] && { echo \"\$d\"; break; }; done")"

  if [[ -n "${fallback}" ]]; then
    echo "${fallback}"
    return 0
  fi

  return 1
}

# ════════════════════════════════════════════════════════════════════════════
# BANNER
# ════════════════════════════════════════════════════════════════════════════
clear
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║       UNDERWATER ROVER — ONE-CLICK LAUNCHER          ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"
info "Pi target  : ${PI_USER}@${PI_HOST}"
info "UDP port   : ${PI_PORT_UDP}"
info "Dashboard  : http://${PI_HOST}:${PI_PORT_WEB}"
info "Camera     : http://${PI_HOST}:${PI_PORT_VIDEO}"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# STEP 1 — Check prerequisites on this PC
# ════════════════════════════════════════════════════════════════════════════
step "Step 1 — Checking PC prerequisites"

if ! command -v sshpass &>/dev/null; then
  warn "sshpass not found — installing..."
  sudo apt-get install -y sshpass -qq || fail "Cannot install sshpass. Run: sudo apt install sshpass"
fi
ok "sshpass found"

if ! command -v rsync &>/dev/null; then
  warn "rsync not found — installing..."
  sudo apt-get install -y rsync -qq || warn "rsync unavailable, falling back to scp"
fi

python3 -c "import serial" 2>/dev/null || {
  warn "pyserial not found on PC — installing..."
  pip3 install pyserial -q
}
ok "pyserial on PC"

# ════════════════════════════════════════════════════════════════════════════
# STEP 2 — Verify Pi is reachable
# ════════════════════════════════════════════════════════════════════════════
step "Step 2 — Connecting to Pi at ${PI_HOST}"

if ! ping -c1 -W3 "${PI_HOST}" &>/dev/null; then
  fail "Cannot ping Pi at ${PI_HOST}. Check ethernet cable and IP."
fi
ok "Pi is reachable"

PI_PYTHON=$(pi_run "python3 --version 2>&1") || fail "SSH to Pi failed"
ok "Pi Python: ${PI_PYTHON}"

# ════════════════════════════════════════════════════════════════════════════
# STEP 3 — Sync files to Pi
# ════════════════════════════════════════════════════════════════════════════
step "Step 3 — Syncing files to Pi → ${PI_ROVER_DIR}/"

pi_run "mkdir -p ${PI_ROVER_DIR}"

# Files that need to run on the Pi
FILES_TO_SYNC=(
  "pi_rover_system.py"
  "pi_web_video_stream.py"
  "hardware_check.py"
  "requirements.txt"
)

for f in "${FILES_TO_SYNC[@]}"; do
  if [[ -f "${SCRIPT_DIR}/${f}" ]]; then
    $SCP "${SCRIPT_DIR}/${f}" "${PI_USER}@${PI_HOST}:${PI_ROVER_DIR}/${f}" \
      && ok "  Synced: ${f}" \
      || warn "  Could not sync: ${f}"
  else
    warn "  Not found locally: ${f} (skipping)"
  fi
done

# Install Pi-side Python dependencies
step "Step 3b — Installing Pi dependencies (flask, pyserial)"
pi_run "pip3 install flask pyserial -q 2>/dev/null || pip install flask pyserial -q 2>/dev/null || true"

if ! EFFECTIVE_UART_PORT="$(detect_pi_uart_port "${UART_PORT}")"; then
  fail "No UART device found on Pi (/dev/serial0, /dev/ttyAMA0, /dev/ttyS0). Enable UART in raspi-config first."
fi

UART_REAL="$(pi_run "readlink -f ${EFFECTIVE_UART_PORT} 2>/dev/null || echo ${EFFECTIVE_UART_PORT}")"
info "Pi UART selected: ${EFFECTIVE_UART_PORT} (maps to ${UART_REAL})"

# Ensure user can access serial device (GPIO UART)
pi_run "sudo usermod -a -G dialout ${PI_USER} 2>/dev/null || true"
pi_run "sudo chmod 660 /dev/serial0 2>/dev/null || true"
pi_run "sudo chmod 660 /dev/ttyAMA0 2>/dev/null || true"
pi_run "sudo chmod 660 /dev/ttyS0 2>/dev/null || true"

SERIAL_GETTY_STATE="$(pi_run "systemctl is-active serial-getty@ttyAMA0.service 2>/dev/null || true")"
if [[ "${SERIAL_GETTY_STATE}" == "active" ]]; then
  warn "serial-getty@ttyAMA0 is active and can steal GPIO UART."
  warn "Disable once on Pi: sudo systemctl disable --now serial-getty@ttyAMA0.service"
fi

ok "Pi dependencies and UART permissions ready"

# ════════════════════════════════════════════════════════════════════════════
# STEP 4 — Stop any previous instance on Pi
# ════════════════════════════════════════════════════════════════════════════
step "Step 4 — Stopping any previous rover process on Pi"
pi_run "pkill -f pi_rover_system.py" 2>/dev/null || true
pi_run "sleep 1" || true
ok "Old processes cleared"

# ════════════════════════════════════════════════════════════════════════════
# STEP 5 — Start pi_rover_system.py on Pi (background, log to file)
# ════════════════════════════════════════════════════════════════════════════
step "Step 5 — Starting pi_rover_system.py on Pi"

# Check UART availability on Pi
UART_AVAILABLE=$(pi_run "test -e ${EFFECTIVE_UART_PORT} && echo yes || echo no")
if [[ "$UART_AVAILABLE" == "yes" ]]; then
  ok "UART ${EFFECTIVE_UART_PORT} available on Pi"
else
  warn "UART ${EFFECTIVE_UART_PORT} not found on Pi — ESP32 link will be inactive"
fi

PI_CMD="cd ${PI_ROVER_DIR} && nohup python3 pi_rover_system.py --listen-ip 0.0.0.0 --listen-port ${PI_PORT_UDP} --uart-port ${EFFECTIVE_UART_PORT} --baud ${BAUD} --web-port ${PI_PORT_WEB} --eth-interface eth0 > /tmp/rover.log 2>&1 </dev/null & echo \$!"

PI_PID="$(pi_run "${PI_CMD}" | tr -dc '0-9')"
[[ -n "${PI_PID}" ]] || fail "Pi rover did not return a PID. Check: ssh ${PI_USER}@${PI_HOST} 'tail -50 /tmp/rover.log'"

if ! pi_run "kill -0 ${PI_PID} 2>/dev/null"; then
  fail "Pi rover process failed to start. Check: ssh ${PI_USER}@${PI_HOST} 'tail -80 /tmp/rover.log'"
fi

ok "Pi rover system started"
info "Pi PID: ${PI_PID}"
info "Pi logs: ssh ${PI_USER}@${PI_HOST} 'tail -f /tmp/rover.log'"

# Wait for Flask to come up
echo -n "  Waiting for dashboard to start"
for i in {1..15}; do
  sleep 1
  echo -n "."
  if pi_run "curl -sf http://localhost:${PI_PORT_WEB}/ -o /dev/null 2>/dev/null"; then
    echo ""
    ok "Dashboard is UP → http://${PI_HOST}:${PI_PORT_WEB}"
    break
  fi
  if [[ $i -eq 15 ]]; then
    echo ""
    warn "Dashboard not responding yet — check logs: ssh ${PI_USER}@${PI_HOST} 'tail -30 /tmp/rover.log'"
  fi
done

# ════════════════════════════════════════════════════════════════════════════
# STEP 5b — Start pi_web_video_stream.py on Pi
# ════════════════════════════════════════════════════════════════════════════
step "Step 5b — Starting camera video stream on Pi"

# Kill any old instance first
pi_run "pkill -f pi_web_video_stream.py 2>/dev/null; true"

VIDEO_CMD="cd ${PI_ROVER_DIR} && nohup python3 pi_web_video_stream.py --port ${PI_PORT_VIDEO} > /tmp/video_stream.log 2>&1 </dev/null & echo \$!"
VIDEO_PID="$(pi_run "${VIDEO_CMD}" | tr -dc '0-9')"

if [[ -n "${VIDEO_PID}" ]]; then
  sleep 2
  if pi_run "kill -0 ${VIDEO_PID} 2>/dev/null"; then
    ok "Camera stream started (PID: ${VIDEO_PID})"
    info "Stream URL : http://${PI_HOST}:${PI_PORT_VIDEO}"
    info "Stream logs: ssh ${PI_USER}@${PI_HOST} 'tail -f /tmp/video_stream.log'"
  else
    warn "Camera stream exited early. Check: ssh ${PI_USER}@${PI_HOST} 'cat /tmp/video_stream.log'"
    VIDEO_PID=""
  fi
else
  warn "Camera stream did not return a PID — continuing without video."
fi


step "Step 6 — Starting PC RC sender (Flysky → UDP → Pi)"

# Auto-detect serial port
SERIAL_PORT="$(find_flysky_port)"

RC_PID=""
if [[ -z "$SERIAL_PORT" ]]; then
  warn "No Flysky adapter detected. Trying sender auto-detect mode..."
  python3 "${SCRIPT_DIR}/pc_rc_sender.py" \
    --pi-ip "${PI_HOST}" \
    --pi-port "${PI_PORT_UDP}" \
    --hz 50 \
    --print-every 20 \
    > /tmp/pc_rc_sender.log 2>&1 &
  RC_PID=$!
else
  ok "Flysky adapter: ${SERIAL_PORT}"

  if [[ ! -r "${SERIAL_PORT}" || ! -w "${SERIAL_PORT}" ]]; then
    warn "No read/write permission on ${SERIAL_PORT}."
    warn "Run once: sudo usermod -a -G dialout ${USER} && newgrp dialout"
  fi

  python3 "${SCRIPT_DIR}/pc_rc_sender.py" \
    --serial-port "${SERIAL_PORT}" \
    --pi-ip "${PI_HOST}" \
    --pi-port "${PI_PORT_UDP}" \
    --hz 50 \
    --print-every 20 \
    > /tmp/pc_rc_sender.log 2>&1 &
  RC_PID=$!
fi

if [[ -n "${RC_PID}" ]]; then
  sleep 2
  if kill -0 "${RC_PID}" 2>/dev/null; then
    ok "RC sender started (PID: ${RC_PID})"
    info "RC logs: tail -f /tmp/pc_rc_sender.log"
  else
    warn "RC sender exited early. Last logs:"
    tail -n 20 /tmp/pc_rc_sender.log 2>/dev/null || true
  fi
fi

# ════════════════════════════════════════════════════════════════════════════
# STEP 7 — Open dashboard in browser
# ════════════════════════════════════════════════════════════════════════════
DASH_URL="http://${PI_HOST}:${PI_PORT_WEB}"
if command -v xdg-open &>/dev/null; then
  xdg-open "${DASH_URL}" &>/dev/null &
elif command -v firefox &>/dev/null; then
  firefox "${DASH_URL}" &>/dev/null &
fi

# ════════════════════════════════════════════════════════════════════════════
# DONE
# ════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}  ╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}  ║  ✓  ALL SYSTEMS GO                                  ║${NC}"
echo -e "${BOLD}  ╠══════════════════════════════════════════════════════╣${NC}"
echo -e "  ║  Dashboard : ${CYAN}${DASH_URL}${NC}"
echo -e "  ║  Camera    : ${CYAN}http://${PI_HOST}:${PI_PORT_VIDEO}${NC}"
echo -e "  ║  Pi logs   : ${CYAN}ssh ${PI_USER}@${PI_HOST} 'tail -f /tmp/rover.log'${NC}"
echo -e "  ║  Ctrl+C    : stops RC sender + Pi rover + camera"
echo -e "${BOLD}  ╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ════════════════════════════════════════════════════════════════════════════
# LIVE LOG — stream Pi logs to terminal
# ════════════════════════════════════════════════════════════════════════════
info "Streaming Pi rover logs (Ctrl+C to stop everything):"
echo "────────────────────────────────────────────────────────"

# Cleanup on exit
cleanup() {
  echo ""
  step "Shutting down..."
  [[ -n "${RC_PID}" ]] && kill "${RC_PID}" 2>/dev/null && ok "RC sender stopped"
  pi_run "pkill -f pi_web_video_stream.py 2>/dev/null || true"
  ok "Camera stream stopped"
  pi_run "pkill -f pi_rover_system.py 2>/dev/null || true"
  ok "Pi rover system stopped"
  ok "Bye!"
}
trap cleanup EXIT INT TERM

# Stream Pi logs live until Ctrl+C
$SSH -tt "${PI_USER}@${PI_HOST}" "tail -F /tmp/rover.log" 2>/dev/null || true
