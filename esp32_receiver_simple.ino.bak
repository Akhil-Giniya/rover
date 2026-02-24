/**
 * ESP32 Receiver - Underwater Rover Unit Control
 *
 * PURPOSE:
 * - Receive RC control data from Raspberry Pi over UART (Serial2)
 * - Parse command packets (channel values and control codes)
 * - Control motor ESCs, propeller speed, lights, and servos
 * - FAILSAFE: stop all motors if Pi loses signal (>1 second) ← WATCHDOG ADDED
 *
 * PINOUT:
 * Serial2 (GPIO16 RX, GPIO17 TX):
 *   - GPIO16 (RX): Connected to Pi GPIO14 (UART TX via /dev/serial0)
 *   - GPIO17 (TX): Connected to Pi GPIO15 (UART RX) for echo/response
 *   - Baud Rate: 115200
 *
 * DATA PROTOCOL:
 * Incoming packets (from Pi, terminated with \n):
 *   - Normal: "1500 1500 1000 2000 1500 1500 1500 1500 1500 1500 1500 1500 1500 1500"
 *             (space-separated uint16 channel values, 1000-2000 μs PWM)
 *   - Failsafe: "NO_SIGNAL" command triggers stopThrusters()
 *
 * Outgoing responses (from this sketch, terminated with \n):
 *   - "ESP32: OK <count>" – sent every 50 packets received
 *   - "ESP32: failsafe_active" – when watchdog triggers
 *   - "ESP32: signal_restored" – when signal is restored after failsafe
 */

// ─── Configuration ───────────────────────────────────────────────────────────
#define SERIAL2_RX_PIN   16       // GPIO16 ← Pi TX
#define SERIAL2_TX_PIN   17       // GPIO17 → Pi RX
#define UART_BAUD        115200
#define WATCHDOG_MS      1000UL   // Kill thrusters after 1 s without packet
// ─────────────────────────────────────────────────────────────────────────────

String line;
unsigned long lastPacketMs = 0;  // millis() timestamp of last valid packet
bool failsafeActive = false;     // Track failsafe state to avoid repeat messages
uint32_t packetCount = 0;        // Total packets received since boot


void stopThrusters() {
  /**
   * FAILSAFE ACTION: Stop all motors immediately.
   *
   * Called when:
   * 1. "NO_SIGNAL" packet received from Pi (software failsafe)
   * 2. Watchdog timer fires (hardware failsafe – no data > WATCHDOG_MS)
   *
   * TODO: Replace stub with actual ESC commands, e.g.:
   *   esc_throttle.writeMicroseconds(1500);
   *   esc_strafe.writeMicroseconds(1500);
   *   lights.off();
   */
  Serial.println("ACTION: STOP_THRUSTERS");
  Serial2.println("ESP32: failsafe_active");
}


void setup() {
  Serial.begin(UART_BAUD);
  Serial2.begin(UART_BAUD, SERIAL_8N1, SERIAL2_RX_PIN, SERIAL2_TX_PIN);
  line.reserve(128);
  lastPacketMs = millis();  // initialise so watchdog doesn't fire immediately
  Serial.println("ESP32 UART receiver ready");
  Serial.printf("  RX GPIO%d  TX GPIO%d  Baud %d  Watchdog %lums\n",
                SERIAL2_RX_PIN, SERIAL2_TX_PIN, UART_BAUD, WATCHDOG_MS);
}


void handlePacket(const String &packet) {
  /**
   * Process a complete RC packet received from Pi.
   *
   * FAILSAFE COMMAND:
   *   Input: "NO_SIGNAL"
   *   → Calls stopThrusters() immediately
   *
   * NORMAL RC DATA:
   *   Input: "1500 1500 1000 2000 ..."
   *   → 14 space-separated uint16 channel values (1000-2000 μs)
   *     Channel[0] = Throttle
   *     Channel[1] = Strafe / lateral
   *     Channel[2] = Camera tilt servo
   *     Channels[3-13] = Lights, arm, mode, etc.
   *
   * TODO: Parse channels and write to ESC/servo hardware:
   *   int ch[14];
   *   ... sscanf(packet.c_str(), ...) ...
   *   esc.writeMicroseconds(ch[0]);
   */
  if (packet == "NO_SIGNAL") {
    if (!failsafeActive) {
      failsafeActive = true;
      stopThrusters();
    }
    return;
  }

  // Valid RC data received – restore from failsafe if needed
  if (failsafeActive) {
    failsafeActive = false;
    Serial.println("ESP32: signal_restored");
    Serial2.println("ESP32: signal_restored");
  }

  packetCount++;
  Serial.print("RC #");
  Serial.print(packetCount);
  Serial.print(": ");
  Serial.println(packet);

  // Echo confirmation to Pi every 50 packets
  if (packetCount % 50 == 0) {
    Serial2.print("ESP32: OK ");
    Serial2.println(packetCount);
  }

  // ─── TODO: Thruster / servo control ──────────────────────────────────────
  // Parse channel values into an integer array and map to hardware:
  //   int channels[14] = {0};
  //   int idx = 0;
  //   char buf[128];
  //   packet.toCharArray(buf, sizeof(buf));
  //   char *tok = strtok(buf, " ");
  //   while (tok && idx < 14) { channels[idx++] = atoi(tok); tok = strtok(NULL, " "); }
  //   esc_throttle.writeMicroseconds(channels[0]);
  //   esc_strafe.writeMicroseconds(channels[1]);
  //   servo_cam.writeMicroseconds(channels[2]);
  // ─────────────────────────────────────────────────────────────────────────
}


void loop() {
  /**
   * Main loop:
   *   1. Read UART chars from Pi and buffer until newline → handlePacket()
   *   2. Watchdog: if WATCHDOG_MS elapsed with no packet → stopThrusters()
   */

  // ─── Step 1: Read incoming UART data ─────────────────────────────────────
  while (Serial2.available() > 0) {
    char c = (char)Serial2.read();

    if (c == '\r') continue;   // Skip CR (Windows line endings)

    if (c == '\n') {
      if (line.length() > 0) {
        lastPacketMs = millis();    // ← reset watchdog on every valid line
        handlePacket(line);
        line = "";
      }
      continue;
    }

    if (line.length() < 120) {
      line += c;
    }
  }

  // ─── Step 2: Watchdog timer ───────────────────────────────────────────────
  // Fire only if we have EVER received a packet (lastPacketMs > 0 trick: we
  // initialise lastPacketMs = millis() in setup(), so the watchdog will not
  // fire until WATCHDOG_MS after boot if there is no data at all.
  if ((millis() - lastPacketMs) >= WATCHDOG_MS) {
    if (!failsafeActive) {
      failsafeActive = true;
      Serial.println("WATCHDOG: No data for 1s, stopping thrusters");
      stopThrusters();
    }
  }
}