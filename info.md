# INFO: Deep Technical Code Reference

This document is code-first and implementation-level. It describes how the repository behaves at runtime from the source code, including protocol formats, thread/task scheduling, state transitions, control equations, and API contracts.

## 1) Source Inventory (Executable/Runtime Relevant)

Python runtime:
- `pc_rc_sender.py`
- `pi_rover_system.py`
- `pi_web_video_stream.py`
- `hardware_check.py`
- `test_udp.py`

ESP32 firmware:
- `esp32_receiver.ino`

Shell automation:
- `launch.sh`
- `crun.sh`
- `ethernet_only_setup.sh`

Verification scripts:
- `verification/verify_dashboard.py`
- `verification/verify_modern_ui.py`

## 2) Protocol-Level Details

### 2.1 Flysky iBUS Frame Contract

Implemented in `pc_rc_sender.py` and validated in `pi_rover_system.py`.

Frame layout (32 bytes total):
- Byte 0..1: header `0x20 0x40`
- Byte 2..29: 14 channels, each uint16 little-endian
- Byte 30..31: checksum uint16 little-endian

Checksum formula used in code:
- `checksum = (0xFFFF - (sum(frame[0:30]) & 0xFFFF)) & 0xFFFF`
- Frame is valid when checksum equals unpacked bytes 30..31.

Channel value domain:
- Nominal 1000..2000 microsecond-equivalent PWM values.

### 2.2 RC Transport (PC -> Pi)

- Protocol: UDP datagrams.
- Typical endpoint: `PiIP:5000`.
- Payload: raw binary iBUS frame (no ASCII conversion).
- Sender rate control in `pc_rc_sender.py`: `--hz` default 50.0.

### 2.3 Bridge Transport (Pi -> ESP32)

- Protocol: UART serial write to `/dev/serial0`.
- Baud: default 115200.
- Pi forwards only frames that satisfy:
  - length == 32
  - first 2 bytes == `0x20 0x40`

### 2.4 ESP32 RC Decode

- Uses `IBusBM` parser on `Serial1` with `IBUSBM_NOTIMER` mode.
- Poll method: `ibus.loop()` in `ibusTask()`.
- New frame detect: compares `ibus.cnt_rec` to prior value.
- On new frame: copies 14 channels to `rcRaw[]`, updates `lastRcMicros`.

## 3) `pc_rc_sender.py` Technical Breakdown

### 3.1 Serial Port Discovery

`auto_detect_serial()` scans in order:
1. `/dev/ttyUSB*`
2. `/dev/ttyACM*`
3. `/dev/ttyS[1-9]*`

First candidate is selected. Multiple candidates emit warning.

### 3.2 Frame Synchronization

`read_ibus_frame()` robust sync behavior:
- Reads byte stream until finds first `0x20`.
- Reads second byte and handles sync-slip cases such as `0x20 0x20 0x40`.
- Reads remaining 30 bytes once header aligned.
- Returns `None` on timeout/partial read to keep loop responsive.

### 3.3 Parse and Validation

`parse_ibus_frame()` enforces:
- exact frame length
- exact header
- checksum match

Returns 14-channel list on success; else `None`.

### 3.4 Rate Limiter

- `interval = 1 / max(hz, 1.0)`
- Uses monotonic `next_send` scheduling.
- Frames may be dropped by limiter if serial source is faster than configured UDP rate.

### 3.5 Failure Model

- Serial open uses `exclusive=True`.
- Reconnect loop catches `serial.SerialException` and generic exceptions.
- Backoff: fixed 2 seconds.

## 4) `pi_rover_system.py` Technical Breakdown

### 4.1 Process-Level Runtime Structure

Main launches four concurrent flows:
1. Flask web server thread pool (HTTP API + dashboard)
2. bridge thread (`bridge_loop`) for UDP RX + UART TX/RX
3. system monitor thread (`SystemMonitor._monitor_loop`)
4. GPIO controller update thread (`GpioController._update_loop`)

Concurrency primitives:
- Shared mutable state guarded by `threading.Lock` in `SharedState.lock`.
- Logs stored in bounded `collections.deque(maxlen=1000)`.

### 4.2 SharedState Data Contract

State variables include:
- Link timing: `last_rc_time`, `last_rc_sender`
- Counters: `packets_rx`, `packets_uart_tx`, `uart_rx_lines`
- Device status: `uart_open`
- GPIO mode flags: `blink_active`, `momentary_active`, `relay_state`
- Switch states: 3 booleans in `switch_states`
- Logs: append-only ID stream with monotonic `next_log_id`

Log entry schema:
- `{"id": int, "ts": "HH:MM:SS", "src": str, "msg": str}`

### 4.3 UDP/UART Bridge Loop

Socket configuration:
- UDP socket with `SO_REUSEADDR`.
- Non-blocking mode + `select.select(..., timeout=0.02)`.
- Effective max poll latency around 20 ms for incoming UDP check.

On packet receive:
1. Update last RC timestamp/sender.
2. Increment `packets_rx`.
3. If packet looks like iBUS (len/header), write raw bytes to UART.
4. Increment `packets_uart_tx` on successful write.

UART RX path:
- Reads available bytes via `uart_dev.in_waiting`.
- Assembles newline-terminated ASCII-ish lines.
- Ignores CR, splits on LF.
- Per line: add ESP32 log and increment `uart_rx_lines`.
- Line buffer hard limit 256 bytes to avoid runaway growth.

No-signal behavior in Pi bridge:
- If no packet ever received: periodic waiting log every 5-second bucket.
- If signal lost for >0.5 s: periodic RC-lost log every 5-second bucket.
- Pi does not synthesize iBUS failsafe packets; it relies on ESP32 timeout watchdog.

### 4.4 iBUS Parser Utility

`parse_ibus_frame(frame)` exists but bridge forwarding path currently checks only len/header before UART write. Deep checksum parse is available but not required in forwarding branch.

### 4.5 System Telemetry Thread

Every 10 seconds reads:
- Pi temperature: `vcgencmd measure_temp`
- Pi volts: `vcgencmd measure_volts`
- RAM usage: `free -h`
- Throttle flags: `vcgencmd get_throttled`

Throttle decode maps bitmask to text:
- under-voltage
- freq-capped
- throttled
- soft-temp-limit

Logs warning when throttled state is not `OK` or `Unknown`.

### 4.6 ServoFilter Algorithm

Per servo filter pipeline:
1. Keep moving window of recent target angles.
2. Convert average angle to duty cycle:
   - `duty = 2.5 + angle/18`
   - clamp to [2.5, 12.5]
3. Deadband suppresses updates when `abs(duty - last_duty) < deadband`.
4. Inactivity settle logic triggers one-time PWM stop (`duty=0`) after `settle_time`.

Configured in controller:
- window_size = 50
- deadband = 0.3
- settle_time = 0.5 s

### 4.7 GPIO Controller Logic

Pin usage:
- Servos: GPIO 12, 13 (`RPi.GPIO.PWM` @ 50 Hz)
- Relay: GPIO 26
- Digital switches: GPIO 17, 27, 22 via `pinctrl set <pin> op dh|dl`

Relay priority in update thread (20 Hz):
1. `momentary_active` => force HIGH
2. else if `blink_active` => HIGH/LOW toggle every 0.5 s
3. else LOW

Thread sleep is 0.05 s, so relay state update granularity is 50 ms.

### 4.8 Flask API Contract

`GET /api/status` response fields:
- `packets_rx: int`
- `packets_uart_tx: int`
- `uart_rx_lines: int`
- `uart_open: bool`
- `last_rc_sender: str`
- `blink_active: bool`
- `relay_state: bool`
- `link_alive: bool` where `last_rc_age_sec < 2.0`
- `last_rc_age_sec: float`
- `ethernet_up: bool`

`GET /api/logs?since=<id>`:
- response `{"logs": [entry,...]}`
- incremental fetch based on log ID cursor

`POST /api/servo/<id>` JSON:
- input `{"angle": int}`
- output `{"status":"ok","id":<id>,"angle":<angle>}`

`POST /api/gpio/momentary` JSON:
- input `{"active": bool}`
- output `{"status":"ok","momentary": bool}`

`POST /api/gpio/blink` JSON:
- input `{"active": bool}`
- output `{"status":"ok","blink": bool}`

`POST /api/gpio/switch/<switch_id>` JSON:
- input `{"active": bool}`
- output `{"status":"ok","switch":<id>,"state": bool}`
- invalid id returns 400

`GET /api/gpio/switches`:
- output `{"switches": [bool,bool,bool]}`

### 4.9 Dashboard Poll/Render Model

Embedded JS polling loops:
- `refreshStatus()` every 500 ms
- `refreshLogs()` every 500 ms

Computed indicators:
- packets/sec estimated from counter delta over elapsed wall time
- signal percent = `min(100, round(pps/60*100))`

Log retention in DOM:
- trims to last 200 rendered rows.

Camera embed URL logic:
- build URL from request host + `:8081/video_feed`

## 5) `pi_web_video_stream.py` Technical Breakdown

### 5.1 Camera Backend Selection

`try_picamera2(width,height,fps)` path:
- imports `Picamera2`
- selects sensor mode index 1 (full FOV note in comments)
- uses XBGR8888 format
- sets frame duration limits from target fps

Fallback path:
- OpenCV `VideoCapture(device)` with width/height/fps hints

### 5.2 MJPEG Streaming

`/video_feed` endpoint returns multipart stream:
- boundary: `frame`
- each frame payload type: `image/jpeg`
- JPEG quality set to 80
- pacing by `frame_delay = 1/fps`

### 5.3 Thread Safety

`CameraStream` wraps read operations in a `Lock` to serialize camera access between request handlers.

## 6) `esp32_receiver.ino` Deep Technical Breakdown

### 6.1 Pin and Hardware Mapping

I2C:
- SDA 8
- SCL 9

ESC pins:
- FL 5, FR 6, RL 7, RR 4

Servo pins:
- front 14, rear 15

iBUS UART:
- RX 16, TX 17

Other:
- calibration button 18 (active LOW)
- RGB pixel 48

### 6.2 Core Runtime State Variables

Critical globals:
- `rcRaw[14]`: latest RC channel values
- `lastRcMicros`: timestamp of last valid iBUS frame
- `mode`: `MANUAL | DEPTH_STAB | PITCH_STAB`
- `armed`: boolean arm state
- Attitude/depth: `rollDeg`, `pitchDeg`, `yawDeg`, `depthM`, `depthTarget`
- Output cache: `escPwmUs[4]`, `servoFrontDeg`, `servoRearDeg`

### 6.3 RC Normalization

`rcNorm(ch)`:
- clamp channel to [1000, 2000]
- map to [-1, +1]

Formula:
- `((v - min)/(max - min))*2 - 1`

### 6.4 Mode and Arm State Machine

Failsafe condition:
- `rcLost = (micros() - lastRcMicros) > RC_LOST_US`
- `RC_LOST_US = 500000` (500 ms)

Mode select from CH6 (when not failsafe):
- `<1300`: MANUAL
- `<1700`: DEPTH_STAB
- else: PITCH_STAB

Failsafe mode override:
- forces `DEPTH_STAB`

Arm select from CH8 (when not failsafe):
- `>1700`: arm true
- `<1300`: arm false

### 6.5 Sensor Fusion Pipeline

IMU update in `updateImuAndDepth(dt)`:
1. Sample accelerometer and gyro `IMU_SAMPLE_COUNT` times (50).
2. Average samples.
3. Apply configurable sign multipliers per axis.
4. Compute accel-based roll/pitch:
   - `rollAcc = atan2(accY, accZ)`
   - `pitchAcc = atan2(-accX, sqrt(accY^2 + accZ^2))`
5. Low-pass filter raw accel/gyro values using alpha `IMU_FILTER_ALPHA`.
6. Complementary filter for roll/pitch with `IMU_COMP_ALPHA`.
7. Integrate gyro Z for yaw and wrap to [-180, 180].
8. If magnetometer available:
   - compute tilt-compensated heading
   - fuse heading into yaw using complementary blending
9. Apply calibration offsets loaded from `Preferences`.

Depth update:
- if depth sensor initialized and read success:
  - update pressure and depth

### 6.6 Calibration Persistence

Namespace keys in `Preferences`:
- namespace: `rov_cal`
- keys: `valid`, `roll`, `pitch`, `yaw`

Calibration trigger:
- button press on pin 18 with debounce 40 ms
- on stable press: saves current raw roll/pitch/yaw as level offsets

### 6.7 PID Controllers

Defined PID structs:
- roll: `{kp=2.0, ki=0.5, kd=0.05}`
- pitch: `{kp=2.0, ki=0.5, kd=0.05}`
- yaw: `{kp=1.5, ki=0.3, kd=0.02}`
- depth: `{kp=2.5, ki=0.6, kd=0.1}`
- output clamp each PID to [-1, 1]

Anti-windup behavior:
- Integrator update is held when unclamped output would saturate.

### 6.8 Stabilizer Correction Shaping

Small-error suppression and large-error scaling:
- deadband thresholds per axis
- high-band thresholds per axis
- correction gain ramps from `STAB_SMALL_CORR_SCALE` to `STAB_HIGH_CORR_SCALE`
- final correction clamped to `STAB_MAX_CORR`

Gating by servo orientation:
- depth correction strongest near vertical thrust angle
- yaw correction strongest near horizontal thrust angle

### 6.9 Thruster and Servo Mixing

Horizontal mixes use arrays:
- `MIX_SURGE`, `MIX_SWAY`, `MIX_YAW`

Vertical mixes use arrays:
- `MIX_ROLL`, `MIX_PITCH`

Mode-dependent composition:
- MANUAL:
  - direct pilot authority
  - roll correction from stick scaled by `MANUAL_ROLL_GAIN`
- DEPTH_STAB / PITCH_STAB:
  - PID corrections added with gating
  - depth target slewed by heave stick (`DEPTH_TARGET_SLEW`)
  - failsafe sets depth target and fallback heave behavior if no depth sensor

Servo geometry projection (non-manual path):
- interpret servo angle as thrust vector tilt
- combine horizontal and vertical components using sin/cos projection

Normalization:
- if any |T| > 1, all T outputs scaled by max absolute value.

Arming safety:
- if not armed, all T outputs forced to zero before ESC write.

### 6.10 Output Rate and Slew Limits

Control loop scheduling:
- task delay `2 ms` -> target around 400 Hz loop cadence.

ESC command conversion:
- deadzone around zero (`ESC_CMD_DEADZONE`)
- map to microseconds [1000, 2000], stop 1500

ESC slew limiter:
- `ESC_SLEW_US_PER_SEC = 1200`
- limits delta per cycle by `rate * dt`

Servo slew limiter:
- `SERVO_SLEW_DEG_PER_SEC = 90`

### 6.11 Tasking and Watchdog

`setup()`:
- initializes serial, watchdog, prefs, calibration
- initializes iBUS, IMU, depth, PWM attachments
- creates tasks pinned to cores:
  - control task on core 0, priority 3
  - telemetry task on core 1, priority 1

Watchdog:
- IDF5 path uses `esp_task_wdt_config_t` with timeout 2000 ms
- both tasks reset watchdog periodically

### 6.12 Status LED Priority

`updateStatusLed()` precedence:
1. RC lost -> red
2. IMU not ok -> magenta
3. low battery -> orange
4. non-manual mode -> blue
5. armed -> green
6. else off

## 7) `hardware_check.py` Technical Behavior

Checks executed:
- Ethernet interface existence and UP state via `ip -brief link show`
- rfkill status parse for Wi-Fi/Bluetooth soft-block state
- UART open/write smoke test (`PI_UART_TEST\n`) if pyserial available
- UDP bind test on target port

Exit code:
- non-zero if any of Ethernet/UART/UDP checks fail.

## 8) `test_udp.py` Technical Behavior

- Binds UDP socket to requested host/port.
- Timeout poll loop with 1-second socket timeout.
- Prints source address and first 100 chars decoded ASCII preview.
- Returns failure exit code if zero packets in duration.

Note:
- For binary iBUS payloads, decode preview is only diagnostic; content may be non-printable.

## 9) Shell Script Technical Behavior

### 9.1 `ethernet_only_setup.sh`

Operations:
1. Block Wi-Fi/Bluetooth via `rfkill`.
2. Disable `wpa_supplicant` and `bluetooth` services.
3. Append `dtoverlay=disable-wifi` and `dtoverlay=disable-bt` to boot config if missing.
4. Configure static eth0 IP using one of:
   - NetworkManager profile file
   - dhcpcd append
   - direct `ip` fallback
5. Force immediate `ip addr flush/add` apply.

Failure model:
- script runs `set -euo pipefail`; aborts on unhandled command failures.

### 9.2 `launch.sh`

- Dependency checks for `sshpass`, `gnome-terminal`, `python3`, `ping`.
- Detects RC USB port by scanning common `/dev/ttyUSB*` and `/dev/ttyACM*`.
- Optionally chmods serial port.
- Opens two new terminals for Pi-side camera and rover scripts over SSH.
- Runs local sender in foreground.

### 9.3 `crun.sh`

- Similar orchestration with simpler flow and retry blocks.
- Starts sender, Pi rover system, Pi video stream, and opens dashboard URL.

## 10) Verification Scripts: Technical Notes

### 10.1 `verification/verify_dashboard.py`

- Uses Playwright sync API.
- Checks title and certain UI texts/controls.
- Drives slider and blink toggle interactions.
- Captures screenshot to `verification/dashboard_gpio.png`.

### 10.2 `verification/verify_modern_ui.py`

- Verifies title and key labels.
- Reads CSS computed styles (background/backdrop blur).
- Captures full page screenshot.

Potential drift:
- Assertions depend on exact title/text strings and can fail if dashboard copy changes.

## 11) Timing and Throughput Summary

PC sender:
- default transmit cadence: 50 Hz (20 ms interval)

Pi bridge:
- select timeout: 20 ms
- log/status polling from UI: 500 ms
- relay/GPIO update loop: 50 ms
- system telemetry logs: every 10 s

ESP32:
- control loop delay: 2 ms target (~400 Hz)
- RC loss watchdog: 500 ms
- watchdog timeout: 2000 ms

## 12) Safety-Critical Technical Paths

Primary safety cutoffs:
- RC timeout on ESP32 (hard behavioral change)
- arming gate forcing zero thruster command when disarmed
- output normalization and slew limiting to avoid step shocks
- watchdog reset to avoid stuck control tasks

Secondary protections:
- bridge only forwards iBUS-like packets to UART
- line-buffer cap on UART RX logs to prevent memory creep

## 13) Extension Points (Code-Centric)

To alter RC semantics:
- edit channel index constants in `esp32_receiver.ino`

To alter stability behavior:
- tune PID gains and STAB_* constants in `esp32_receiver.ino`

To alter Pi dashboard behavior/API:
- edit routes and `DASHBOARD_HTML` JS in `pi_rover_system.py`

To alter transport rates:
- sender `--hz`, Pi select timeout, ESP32 loop delay and slew constants

To harden API security:
- add auth layer in Flask routes (currently none)

---

Last updated: 2026-03-19
