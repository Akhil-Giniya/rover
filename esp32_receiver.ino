#ifndef ARDUINO_USB_CDC_ON_BOOT
#define ARDUINO_USB_CDC_ON_BOOT 1
#endif

#include <Arduino.h>
#include <Wire.h>
#include <IBusBM.h>
#include <MPU9250_WE.h>
#include <MS5837.h>
#include <Preferences.h>
#include <ESP32Servo.h>
#include <Adafruit_NeoPixel.h>
#include <esp_task_wdt.h>
#include <esp_idf_version.h>

#define DBG_PRINTLN(msg) do { Serial.println(msg); } while (0)
#define DBG_PRINTF(...) do { Serial.printf(__VA_ARGS__); } while (0)

// ---------- Pin map ----------
constexpr uint8_t IMU_I2C_ADDR = 0x68; // AD0 low. Use 0x69 if AD0 is high.

constexpr int PIN_I2C_SDA  = 8;
constexpr int PIN_I2C_SCL  = 9;

constexpr int PIN_ESC1 = 5;   // Front-Left
constexpr int PIN_ESC2 = 6;   // Front-Right
constexpr int PIN_ESC3 = 7;   // Rear-Left
constexpr int PIN_ESC4 = 4;   // Rear-Right

constexpr int PIN_SERVO_FRONT = 14;
constexpr int PIN_SERVO_REAR  = 15;

constexpr int PIN_IBUS_RX = 16;
constexpr int PIN_IBUS_TX = 17;
constexpr int PIN_CAL_BUTTON = 18; // active LOW, wire button to GND
constexpr int PIN_RGB = 48;
constexpr int NUM_PIXELS = 1;
constexpr bool IBUS_DEBUG_ONLY = false; // set false for full ROV control

// ---------- RC mapping ----------
static IBusBM ibus;
volatile uint16_t rcRaw[14];
uint32_t lastRcMicros = 0;
constexpr uint32_t RC_LOST_US = 500000;
// RC channel map (0-based iBus indices)
// Adjust here if your transmitter uses a different channel order.
constexpr int RC_CH_ROLL        = 0; // CH1 (aileron)
constexpr int RC_CH_PITCH       = 1; // CH2 (elevator)
constexpr int RC_CH_HEAVE       = 2; // CH3 (throttle/heave)
constexpr int RC_CH_YAW         = 3; // CH4 (rudder)
constexpr int RC_CH_SERVO_FRONT = 4; // CH5
constexpr int RC_CH_MODE        = 5; // CH6 (3-position mode)
constexpr int RC_CH_SERVO_REAR  = 6; // CH7
constexpr int RC_CH_ARM         = 7; // CH8 (arm/disarm)

inline float rcNorm(int ch, float min=1000, float max=2000) {
  float v = constrain(rcRaw[ch], 1000, 2000);
  return (v - min) / (max - min) * 2.f - 1.f; // -1..1
}

// ---------- Sensors ----------
MPU9250_WE imu9250 = MPU9250_WE(&Wire, IMU_I2C_ADDR);
MPU6500_WE imu6500 = MPU6500_WE(&Wire, IMU_I2C_ADDR);
MPU6500_WE* imu = nullptr; // active IMU instance (9250 or 6500)
MS5837 depthSensor;
Preferences prefs;

// ---------- ESC / Servo ----------
Servo esc1, esc2, esc3, esc4;
Servo servoFront, servoRear;
Adafruit_NeoPixel pixel(NUM_PIXELS, PIN_RGB, NEO_GRB + NEO_KHZ800);

// ---------- PID ----------
struct PID {
  float kp, ki, kd;
  float outMin, outMax;
  float iTerm = 0, prev = 0;
  float update(float setpoint, float meas, float dt) {
    float err = setpoint - meas;
    float d = (err - prev) / max(dt, 1e-4f);
    prev = err;
    float p = err * kp;
    float iNew = iTerm + err * ki * dt;
    float out = p + iNew + d * kd;
    if (out > outMax || out < outMin) {
      out = constrain(out, outMin, outMax);
      // Anti-windup: hold integrator when saturated.
    } else {
      iTerm = constrain(iNew, outMin, outMax);
    }
    return out;
  }
  void reset() { iTerm = prev = 0; }
};

PID pidRoll  { 2.0f, 0.5f, 0.05f, -1, 1 };
PID pidPitch { 2.0f, 0.5f, 0.05f, -1, 1 };
PID pidYaw   { 1.5f, 0.3f, 0.02f, -1, 1 };
PID pidDepth { 2.5f, 0.6f, 0.1f,  -1, 1 };

// ---------- State ----------
float rollDeg=0, pitchDeg=0, yawDeg=0;
float depthM = 0, depthTarget = 0;
float depthPressureMbar = 0;
float servoFrontDeg=90, servoRearDeg=90;
float imuAccX = 0, imuAccY = 0, imuAccZ = 0;
float imuGyrX = 0, imuGyrY = 0, imuGyrZ = 0;
float imuRollRawFilt = 0, imuPitchRawFilt = 0, imuYawRawFilt = 0;
float magX = 0, magY = 0, magZ = 0;
float headingDeg = 0;
float rollLevelOffsetDeg = 0;
float pitchLevelOffsetDeg = 0;
float yawLevelOffsetDeg = 0;
int escPwmUs[4] = {1500, 1500, 1500, 1500};
bool imuOk = false;
bool depthOk = false;
bool imuFilterPrimed = false;
bool magOk = false;
bool prefsReady = false;
bool armed = false;
bool lowBattery = false;
float batteryVolts = 0.0f;
float currentAmps = 0.0f;
uint32_t lastRgbUpdate = 0;
enum Mode { MANUAL, DEPTH_STAB, PITCH_STAB };
Mode mode = MANUAL;

// Thruster mix for layout:
// Front: T1 (FL, CW) ---- T2 (FR, CCW)
// Back:  T3 (BL, CCW) --- T4 (BR, CW)
constexpr float MIX_SURGE[4] = {+1.f, +1.f, +1.f, +1.f};
constexpr float MIX_SWAY[4]  = {+1.f, -1.f, +1.f, -1.f};
// Yaw mapping: left side (T1,T3) vs right side (T2,T4)
constexpr float MIX_YAW[4]   = {+1.f, -1.f, +1.f, -1.f};
constexpr float MIX_ROLL[4]  = {-1.f, +1.f, -1.f, +1.f}; // left -, right +
constexpr float MIX_PITCH[4] = {-1.f, -1.f, +1.f, +1.f}; // front -, back +
constexpr float SERVO_DEG_FORWARD = 45.f;   // fully horizontal
constexpr float SERVO_DEG_UP      = 135.f;  // fully vertical
constexpr int ESC_US_REV  = 1000;
constexpr int ESC_US_STOP = 1500;
constexpr int ESC_US_FWD  = 2000;
constexpr float ESC_CMD_DEADZONE = 0.05f; // around zero command -> 1500 us
constexpr float ESC_SLEW_US_PER_SEC = 1200.0f;
constexpr float SERVO_SLEW_DEG_PER_SEC = 90.0f;
constexpr float MANUAL_ROLL_GAIN = 0.6f;   // CH1 roll authority in MANUAL mode
constexpr uint32_t CAL_BTN_DEBOUNCE_MS = 40;
constexpr const char *CAL_NS = "rov_cal";
constexpr const char *CAL_KEY_VALID = "valid";
constexpr const char *CAL_KEY_ROLL = "roll";
constexpr const char *CAL_KEY_PITCH = "pitch";
constexpr const char *CAL_KEY_YAW = "yaw";
constexpr float IMU_FILTER_ALPHA = 0.25f; // 0..1 (lower = smoother, slower)
constexpr float STAB_SMALL_CORR_SCALE = 0.30f;
constexpr float STAB_HIGH_CORR_SCALE  = 0.85f;
constexpr float STAB_MAX_CORR         = 0.80f;
constexpr float STAB_ROLL_DB_DEG      = 1.5f;
constexpr float STAB_ROLL_HIGH_DEG    = 12.0f;
constexpr float STAB_PITCH_DB_DEG     = 1.5f;
constexpr float STAB_PITCH_HIGH_DEG   = 12.0f;
constexpr float STAB_DEPTH_DB_M       = 0.03f;
constexpr float STAB_DEPTH_HIGH_M     = 0.30f;
constexpr float STAB_YAWRATE_DB_DPS   = 3.0f;
constexpr float STAB_YAWRATE_HIGH_DPS = 35.0f;
constexpr float STAB_DEPTH_AXIS_START = 110.0f; // begin depth correction
constexpr float STAB_DEPTH_AXIS_FULL  = 130.0f; // full depth correction
constexpr float STAB_YAW_AXIS_FULL    = 50.0f;  // full yaw correction
constexpr float STAB_YAW_AXIS_OFF     = 80.0f;  // yaw correction off by here
constexpr float DEPTH_TARGET_SLEW     = 0.002f;
constexpr bool HAVE_BATT_SENSE        = false;
constexpr int PIN_BATT_ADC            = -1; // set to your voltage sensor ADC pin
constexpr float BATT_LOW_VOLTS        = 10.5f;
constexpr bool HAVE_CURR_SENSE        = false;
constexpr int PIN_CURR_ADC            = -1; // set to your ACS758 ADC pin
constexpr float ADC_REF_VOLT          = 3.3f;
constexpr int ADC_MAX_COUNTS          = 4095;
constexpr float VOLTAGE_DIV_RATIO     = 5.0f;  // set to your module divider ratio
constexpr float ACS_ZERO_VOLT         = 1.65f; // Vcc/2 for ACS758
constexpr float ACS_SENS_V_PER_A      = 0.040f; // V/A, set per ACS758 model
constexpr float FAILSAFE_DEPTH_TARGET = -0.2f;  // meters (negative = up in your convention)
constexpr float FAILSAFE_HEAVE_CMD    = -0.2f;  // used if no depth sensor
constexpr uint32_t WDT_TIMEOUT_MS     = 2000;
constexpr float IMU_ACC_X_SIGN = 1.0f;
constexpr float IMU_ACC_Y_SIGN = 1.0f;
constexpr float IMU_ACC_Z_SIGN = 1.0f;
constexpr float IMU_GYR_X_SIGN = 1.0f;
constexpr float IMU_GYR_Y_SIGN = 1.0f;
constexpr float IMU_GYR_Z_SIGN = 1.0f;
constexpr float IMU_MAG_X_SIGN = 1.0f;
constexpr float IMU_MAG_Y_SIGN = 1.0f;
constexpr float IMU_MAG_Z_SIGN = 1.0f;
constexpr float MAG_DECLINATION_DEG = 0.0f;
constexpr int IMU_SAMPLE_COUNT = 50; // small sample average per update
constexpr float IMU_COMP_ALPHA = 0.98f; // complementary filter (gyro weight)

// ---------- Utility ----------
void calibrationButtonTask(float rawRollDeg, float rawPitchDeg, float rawYawDeg);
void updateStatusLed(bool isArmed, Mode curMode, bool rcLost, bool imuOkFlag, bool lowBatt);
void updateImuAndDepth(float dt);
void initPreferences();
void initWatchdog();
void feedWatchdog();
float readCurrentAmps();
void printIbusChannels();

float mapf(float x, float inMin, float inMax, float outMin, float outMax) {
  return (x - inMin)*(outMax - outMin)/(inMax - inMin) + outMin;
}

inline float lowPass(float prev, float input, float alpha) {
  return prev + alpha * (input - prev);
}

inline float slewLimit(float current, float target, float maxDelta) {
  if (target > current + maxDelta) return current + maxDelta;
  if (target < current - maxDelta) return current - maxDelta;
  return target;
}

inline float ramp01(float x, float x0, float x1) {
  return constrain((x - x0) / max(1e-4f, x1 - x0), 0.0f, 1.0f);
}

inline float stabilizerCorrection(float pidOut, float errAbs, float deadband, float highBand) {
  if (errAbs <= deadband) return 0.0f; // ignore very small errors
  float w = ramp01(errAbs, deadband, highBand);
  float scale = STAB_SMALL_CORR_SCALE + w * (STAB_HIGH_CORR_SCALE - STAB_SMALL_CORR_SCALE);
  return constrain(pidOut * scale, -STAB_MAX_CORR, STAB_MAX_CORR);
}

inline float depthAxisFactor(float servoAvgDeg) {
  // Depth correction is strongest near vertical (~135 deg).
  return ramp01(servoAvgDeg, STAB_DEPTH_AXIS_START, STAB_DEPTH_AXIS_FULL);
}

inline float yawAxisFactor(float servoAvgDeg) {
  // Yaw correction is strongest near horizontal (~45 deg).
  return constrain((STAB_YAW_AXIS_OFF - servoAvgDeg) / max(1e-4f, STAB_YAW_AXIS_OFF - STAB_YAW_AXIS_FULL), 0.0f, 1.0f);
}

void updateImuAndDepth(float dt) {
  if (imuOk && imu != nullptr) {
    xyzFloat accSum = {0, 0, 0};
    xyzFloat gyrSum = {0, 0, 0};
    for (int i = 0; i < IMU_SAMPLE_COUNT; i++) {
      xyzFloat acc = imu->getGValues();
      xyzFloat gyr = imu->getGyrValues();
      accSum.x += acc.x; accSum.y += acc.y; accSum.z += acc.z;
      gyrSum.x += gyr.x; gyrSum.y += gyr.y; gyrSum.z += gyr.z;
    }
    float invN = 1.0f / float(IMU_SAMPLE_COUNT);
    float accX = accSum.x * invN * IMU_ACC_X_SIGN;
    float accY = accSum.y * invN * IMU_ACC_Y_SIGN;
    float accZ = accSum.z * invN * IMU_ACC_Z_SIGN;
    float gyrX = gyrSum.x * invN * IMU_GYR_X_SIGN;
    float gyrY = gyrSum.y * invN * IMU_GYR_Y_SIGN;
    float gyrZ = gyrSum.z * invN * IMU_GYR_Z_SIGN;

    // Compute roll/pitch from accelerometer (g)
    float rollAcc = atan2f(accY, accZ) * 180.0f / PI;
    float pitchAcc = atan2f(-accX, sqrtf(accY * accY + accZ * accZ)) * 180.0f / PI;

    bool firstSample = !imuFilterPrimed;
    if (firstSample) {
      imuAccX = accX; imuAccY = accY; imuAccZ = accZ;
      imuGyrX = gyrX; imuGyrY = gyrY; imuGyrZ = gyrZ;
      imuRollRawFilt = rollAcc;
      imuPitchRawFilt = pitchAcc;
      imuYawRawFilt = 0.0f;
      imuFilterPrimed = true;
    } else {
      imuAccX = lowPass(imuAccX, accX, IMU_FILTER_ALPHA);
      imuAccY = lowPass(imuAccY, accY, IMU_FILTER_ALPHA);
      imuAccZ = lowPass(imuAccZ, accZ, IMU_FILTER_ALPHA);
      imuGyrX = lowPass(imuGyrX, gyrX, IMU_FILTER_ALPHA);
      imuGyrY = lowPass(imuGyrY, gyrY, IMU_FILTER_ALPHA);
      imuGyrZ = lowPass(imuGyrZ, gyrZ, IMU_FILTER_ALPHA);
    }

    // Complementary filter for roll/pitch using gyro integration
    float rollGyro = imuRollRawFilt + imuGyrX * dt;
    float pitchGyro = imuPitchRawFilt + imuGyrY * dt;
    imuRollRawFilt = IMU_COMP_ALPHA * rollGyro + (1.0f - IMU_COMP_ALPHA) * rollAcc;
    imuPitchRawFilt = IMU_COMP_ALPHA * pitchGyro + (1.0f - IMU_COMP_ALPHA) * pitchAcc;

    // Yaw from gyro integration; optionally correct with magnetometer
    imuYawRawFilt += imuGyrZ * dt;
    if (imuYawRawFilt > 180.0f) imuYawRawFilt -= 360.0f;
    if (imuYawRawFilt < -180.0f) imuYawRawFilt += 360.0f;

    if (magOk) {
      xyzFloat mag = imu9250.getMagValues();
      magX = mag.x * IMU_MAG_X_SIGN;
      magY = mag.y * IMU_MAG_Y_SIGN;
      magZ = mag.z * IMU_MAG_Z_SIGN;
      // Tilt-compensated heading using roll/pitch
      float rollRad = imuRollRawFilt * DEG_TO_RAD;
      float pitchRad = imuPitchRawFilt * DEG_TO_RAD;
      float magXComp = magX * cosf(pitchRad) + magZ * sinf(pitchRad);
      float magYComp = magX * sinf(rollRad) * sinf(pitchRad) + magY * cosf(rollRad) - magZ * sinf(rollRad) * cosf(pitchRad);
      float heading = atan2f(magYComp, magXComp) * 180.0f / PI + MAG_DECLINATION_DEG;
      if (heading < 0) heading += 360.0f;
      if (heading >= 360.0f) heading -= 360.0f;
      headingDeg = heading;

      // Fuse magnetometer heading into yaw
      float yawMag = headingDeg;
      float yawGyro = imuYawRawFilt;
      // Wrap gyro yaw to 0..360 for fusion
      if (yawGyro < 0) yawGyro += 360.0f;
      float yawFuse = IMU_COMP_ALPHA * yawGyro + (1.0f - IMU_COMP_ALPHA) * yawMag;
      if (yawFuse >= 360.0f) yawFuse -= 360.0f;
      if (yawFuse > 180.0f) yawFuse -= 360.0f;
      imuYawRawFilt = yawFuse;
      if (firstSample) {
        imuYawRawFilt = headingDeg;
      }
    } else {
      magX = magY = magZ = 0.0f;
      headingDeg = 0.0f;
    }

    calibrationButtonTask(imuRollRawFilt, imuPitchRawFilt, imuYawRawFilt);
    rollDeg = imuRollRawFilt - rollLevelOffsetDeg;
    pitchDeg = imuPitchRawFilt - pitchLevelOffsetDeg;
    yawDeg = imuYawRawFilt - yawLevelOffsetDeg;
  } else {
    imuAccX = imuAccY = imuAccZ = 0.0f;
    imuGyrX = imuGyrY = imuGyrZ = 0.0f;
    rollDeg = pitchDeg = yawDeg = 0.0f;
    imuRollRawFilt = imuPitchRawFilt = imuYawRawFilt = 0.0f;
    imuFilterPrimed = false;
    magX = magY = magZ = 0.0f;
    headingDeg = 0.0f;
  }

  if (depthOk) {
    if (depthSensor.read() == 0) {
      depthPressureMbar = depthSensor.getPressure();
      depthM = depthSensor.getDepth();
    }
  } else {
    depthPressureMbar = 0.0f;
    depthM = 0.0f;
  }
}

float readBatteryVolts() {
  if (!HAVE_BATT_SENSE || PIN_BATT_ADC < 0) return 0.0f;
  int raw = analogRead(PIN_BATT_ADC);
  float vAdc = (float(raw) / float(ADC_MAX_COUNTS)) * ADC_REF_VOLT;
  return vAdc * VOLTAGE_DIV_RATIO;
}

float readCurrentAmps() {
  if (!HAVE_CURR_SENSE || PIN_CURR_ADC < 0) return 0.0f;
  int raw = analogRead(PIN_CURR_ADC);
  float vAdc = (float(raw) / float(ADC_MAX_COUNTS)) * ADC_REF_VOLT;
  return (vAdc - ACS_ZERO_VOLT) / ACS_SENS_V_PER_A;
}

void updateStatusLed(bool isArmed, Mode curMode, bool rcLost, bool imuOkFlag, bool lowBatt) {
  if (millis() - lastRgbUpdate < 50) return;
  lastRgbUpdate = millis();

  uint32_t color = 0;
  if (rcLost) {
    color = pixel.Color(255, 0, 0);         // red
  } else if (!imuOkFlag) {
    color = pixel.Color(255, 0, 255);       // magenta
  } else if (lowBatt) {
    color = pixel.Color(255, 80, 0);        // orange
  } else if (curMode != MANUAL) {
    color = pixel.Color(0, 0, 255);         // blue (depth hold modes)
  } else if (isArmed) {
    color = pixel.Color(0, 255, 0);         // green
  } else {
    color = pixel.Color(0, 0, 0);           // off
  }

  pixel.setPixelColor(0, color);
  pixel.show();
}

void initPreferences() {
  prefsReady = prefs.begin(CAL_NS, false);
  if (!prefsReady) {
    DBG_PRINTLN("BOOT: prefs init FAILED");
  }
}

void initWatchdog() {
#if ESP_IDF_VERSION_MAJOR >= 5
  esp_task_wdt_config_t config = {};
  config.timeout_ms = WDT_TIMEOUT_MS;
  config.idle_core_mask = (1 << portNUM_PROCESSORS) - 1;
  config.trigger_panic = true;
  esp_task_wdt_init(&config);
#else
  esp_task_wdt_init(WDT_TIMEOUT_MS / 1000, true);
#endif
  esp_task_wdt_add(NULL); // add current task
}

void feedWatchdog() {
  esp_task_wdt_reset();
}

void loadLevelCalibration() {
  if (!prefsReady) {
    rollLevelOffsetDeg = 0.0f;
    pitchLevelOffsetDeg = 0.0f;
    yawLevelOffsetDeg = 0.0f;
    DBG_PRINTLN("CAL: prefs not ready, using zeros");
    return;
  }
  bool valid = prefs.getBool(CAL_KEY_VALID, false);
  if (valid) {
    rollLevelOffsetDeg = prefs.getFloat(CAL_KEY_ROLL, 0.0f);
    pitchLevelOffsetDeg = prefs.getFloat(CAL_KEY_PITCH, 0.0f);
    yawLevelOffsetDeg = prefs.getFloat(CAL_KEY_YAW, 0.0f);
  } else {
    rollLevelOffsetDeg = 0.0f;
    pitchLevelOffsetDeg = 0.0f;
    yawLevelOffsetDeg = 0.0f;
  }
  DBG_PRINTF("CAL: load valid=%d roll=%.2f pitch=%.2f yaw=%.2f\n",
             valid ? 1 : 0, rollLevelOffsetDeg, pitchLevelOffsetDeg, yawLevelOffsetDeg);
}

void saveLevelCalibration(float rawRollDeg, float rawPitchDeg, float rawYawDeg) {
  if (!prefsReady) return;
  rollLevelOffsetDeg = rawRollDeg;
  pitchLevelOffsetDeg = rawPitchDeg;
  yawLevelOffsetDeg = rawYawDeg;
  size_t w1 = prefs.putFloat(CAL_KEY_ROLL, rollLevelOffsetDeg);
  size_t w2 = prefs.putFloat(CAL_KEY_PITCH, pitchLevelOffsetDeg);
  size_t w3 = prefs.putFloat(CAL_KEY_YAW, yawLevelOffsetDeg);
  bool ok = prefs.putBool(CAL_KEY_VALID, true);
  DBG_PRINTF("CAL: save roll=%.2f pitch=%.2f yaw=%.2f (w=%u/%u/%u ok=%d)\n",
             rollLevelOffsetDeg, pitchLevelOffsetDeg, yawLevelOffsetDeg,
             unsigned(w1), unsigned(w2), unsigned(w3), ok ? 1 : 0);
}

void calibrationButtonTask(float rawRollDeg, float rawPitchDeg, float rawYawDeg) {
  static bool lastRawPressed = false;
  static bool stablePressed = false;
  static uint32_t lastEdgeMs = 0;

  bool rawPressed = (digitalRead(PIN_CAL_BUTTON) == LOW);
  if (rawPressed != lastRawPressed) {
    lastRawPressed = rawPressed;
    lastEdgeMs = millis();
  }

  if ((millis() - lastEdgeMs) >= CAL_BTN_DEBOUNCE_MS && rawPressed != stablePressed) {
    stablePressed = rawPressed;
    if (stablePressed) {
      saveLevelCalibration(rawRollDeg, rawPitchDeg, rawYawDeg);
    }
  }
}

void ibusTask() {
  static uint8_t lastCnt = 0;
  ibus.loop(); // using IBUSBM_NOTIMER mode, so poll parser manually
  uint8_t cnt = ibus.cnt_rec;
  if (cnt != lastCnt) {
    lastCnt = cnt;
    for (int i = 0; i < 14; i++) rcRaw[i] = ibus.readChannel(i);
    lastRcMicros = micros();
  }
}

void printIbusChannels() {
  DBG_PRINTF(
    "IBUS: CH1=%u CH2=%u CH3=%u CH4=%u CH5=%u CH6=%u CH7=%u CH8=%u "
    "CH9=%u CH10=%u CH11=%u CH12=%u CH13=%u CH14=%u\n",
    rcRaw[0], rcRaw[1], rcRaw[2], rcRaw[3], rcRaw[4], rcRaw[5], rcRaw[6],
    rcRaw[7], rcRaw[8], rcRaw[9], rcRaw[10], rcRaw[11], rcRaw[12], rcRaw[13]
  );
}


// ---------- Sensors init ----------
bool initIMU() {
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  // Try full MPU9250 init first (includes magnetometer). If it fails,
  // fallback to MPU6500 init using public API.
  if (imu9250.init()) {
    imu = &imu9250;
    magOk = imu9250.initMagnetometer();
    if (magOk) {
      imu9250.setMagOpMode(AK8963_CONT_MODE_100HZ);
    }
    DBG_PRINTLN("BOOT: IMU MPU9250 mode");
  } else if (imu6500.init()) {
    imu = &imu6500;
    magOk = false;
    DBG_PRINTLN("BOOT: IMU MPU6500 mode");
  } else {
    imu = nullptr;
    magOk = false;
    return false;
  }
  DBG_PRINTF("BOOT: IMU WHO_AM_I=0x%02X\n", imu->whoAmI());
  if (imu == &imu9250) {
    DBG_PRINTF("BOOT: MAG init %s\n", magOk ? "OK" : "FAILED");
    if (magOk) {
      DBG_PRINTF("BOOT: MAG WHO_AM_I=0x%02X\n", imu9250.whoAmIMag());
    }
  }
  imu->autoOffsets();
  imu->setAccRange(MPU9250_ACC_RANGE_4G);
  imu->setGyrRange(MPU9250_GYRO_RANGE_500);
  return true;
}

bool initDepth() {
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  if (!depthSensor.begin(0)) {   // 0 = MS5837-30
    return false;
  }
  depthSensor.setDensity(0.997f);
  return true;
}


// ---------- ESC PWM ----------
inline int throttleToEscUs(float t) {
  t = constrain(t, -1.f, 1.f);
  if (fabsf(t) <= ESC_CMD_DEADZONE) return ESC_US_STOP;
  if (t > 0.f) {
    return int(mapf(t, ESC_CMD_DEADZONE, 1.f, float(ESC_US_STOP), float(ESC_US_FWD)));
  }
  return int(mapf(t, -1.f, -ESC_CMD_DEADZONE, float(ESC_US_REV), float(ESC_US_STOP)));
}

void sendEscs(float t1,float t2,float t3,float t4) {
  int targetUs[4] = {
    throttleToEscUs(t1),
    throttleToEscUs(t2),
    throttleToEscUs(t3),
    throttleToEscUs(t4)
  };

  static float escSlewUs[4] = {
    float(ESC_US_STOP), float(ESC_US_STOP), float(ESC_US_STOP), float(ESC_US_STOP)
  };
  static uint32_t lastUpdateUs = 0;
  uint32_t nowUs = micros();
  float dtS = (lastUpdateUs == 0) ? 0.0025f : (nowUs - lastUpdateUs) * 1e-6f;
  lastUpdateUs = nowUs;
  dtS = constrain(dtS, 0.001f, 0.05f);
  float maxDeltaUs = ESC_SLEW_US_PER_SEC * dtS;

  for (int i = 0; i < 4; i++) {
    escSlewUs[i] = slewLimit(escSlewUs[i], float(targetUs[i]), maxDeltaUs);
    escPwmUs[i] = int(escSlewUs[i] + 0.5f);
  }

  esc1.writeMicroseconds(escPwmUs[0]);
  esc2.writeMicroseconds(escPwmUs[1]);
  esc3.writeMicroseconds(escPwmUs[2]);
  esc4.writeMicroseconds(escPwmUs[3]);
}

// ---------- Servo ----------
inline void writeServo(Servo &s, float deg) {
  deg = constrain(deg, SERVO_DEG_FORWARD, SERVO_DEG_UP);
  s.writeMicroseconds(mapf(deg, 0, 180, 500, 2500));
}

// ---------- Control loop (Core 0) ----------
void controlLoop(void*) {
  uint32_t lastLoopMicros = micros();
  uint32_t lastDebug = millis();
  esp_task_wdt_add(NULL);
  for (;;) {
    uint32_t now = micros();
    float dt = (now - lastLoopMicros) * 1e-6f;
    lastLoopMicros = now;
    dt = constrain(dt, 0.001f, 0.05f);
    feedWatchdog();
    ibusTask();
    bool rcLost = (now - lastRcMicros) > RC_LOST_US;
    bool failsafe = rcLost;
    int modePwm = rcRaw[RC_CH_MODE];
    if (failsafe) {
      mode = DEPTH_STAB;
    } else if (modePwm < 1300) {
      mode = MANUAL;
    } else if (modePwm < 1700) {
      mode = DEPTH_STAB;
    } else {
      mode = PITCH_STAB;
    }

    int armPwm = rcRaw[RC_CH_ARM];
    if (!failsafe) {
      if (armPwm > 1700) {
        armed = true;
      } else if (armPwm < 1300) {
        armed = false;
      }
    }

    updateImuAndDepth(dt);
    if (HAVE_BATT_SENSE) {
      batteryVolts = readBatteryVolts();
      lowBattery = batteryVolts > 0.1f && batteryVolts < BATT_LOW_VOLTS;
    } else {
      batteryVolts = 0.0f;
      lowBattery = false;
    }
    if (HAVE_CURR_SENSE) {
      currentAmps = readCurrentAmps();
    } else {
      currentAmps = 0.0f;
    }


    float cmdRoll  = rcLost ? 0 : rcNorm(RC_CH_ROLL);
    float cmdPitch = rcLost ? 0 : rcNorm(RC_CH_PITCH);
    float cmdHeave = rcLost ? 0 : rcNorm(RC_CH_HEAVE);
    float cmdYaw   = rcLost ? 0 : rcNorm(RC_CH_YAW);
    float cmdSrvF  = rcLost ? 0 : rcNorm(RC_CH_SERVO_FRONT);
    float cmdSrvR  = rcLost ? 0 : rcNorm(RC_CH_SERVO_REAR);

    if (failsafe) {
      cmdRoll = 0.0f;
      cmdPitch = 0.0f;
      cmdYaw = 0.0f;
      cmdHeave = 0.0f;
      cmdSrvF = 0.0f;
      cmdSrvR = 0.0f;
    }

    float surge = cmdPitch;
    float sway  = 0.0f;
    float yaw   = cmdYaw;
    float heave = cmdHeave;

    float rollCorr=0, pitchCorr=0, yawCorr=0, depthCorr=0;

    float servoFrontTargetDeg;
    float servoRearTargetDeg;
    if (failsafe) {
      servoFrontTargetDeg = SERVO_DEG_UP;
      servoRearTargetDeg  = SERVO_DEG_UP;
    } else if (mode == MANUAL) {
      servoFrontTargetDeg = mapf(cmdSrvF, -1, 1, SERVO_DEG_FORWARD, SERVO_DEG_UP);
      servoRearTargetDeg  = mapf(cmdSrvR, -1, 1, SERVO_DEG_FORWARD, SERVO_DEG_UP);
    } else if (mode == DEPTH_STAB) {
      float Hmag = max(0.001f, sqrtf(surge*surge + sway*sway));
      float Vmag = fabs(cmdHeave);
      float theta = SERVO_DEG_FORWARD + atan2f(Vmag, Hmag) * 180.0f / PI;
      theta = constrain(theta, SERVO_DEG_FORWARD, SERVO_DEG_UP);
      servoFrontTargetDeg = theta;
      servoRearTargetDeg  = theta;
    } else { // PITCH_STAB
      // Front servo fixed vertical, rear servo blends between horizontal/vertical.
      float Hmag = max(0.001f, fabsf(surge) + fabsf(cmdYaw));
      float Vmag = fabsf(cmdHeave);
      float theta = SERVO_DEG_FORWARD + atan2f(Vmag, Hmag) * 180.0f / PI;
      theta = constrain(theta, SERVO_DEG_FORWARD, SERVO_DEG_UP);
      servoFrontTargetDeg = SERVO_DEG_UP;
      servoRearTargetDeg  = theta;
    }

    float maxServoDelta = SERVO_SLEW_DEG_PER_SEC * dt;
    servoFrontDeg = slewLimit(servoFrontDeg, servoFrontTargetDeg, maxServoDelta);
    servoRearDeg  = slewLimit(servoRearDeg,  servoRearTargetDeg,  maxServoDelta);

    if (mode == MANUAL) {
      depthTarget = depthM;
      heave = cmdHeave;
      // MANUAL: roll is direct stick command (no stabilization loop).
      rollCorr = cmdRoll * MANUAL_ROLL_GAIN;
      pitchCorr = yawCorr = depthCorr = 0;
    } else {
      if (failsafe) {
        depthTarget = FAILSAFE_DEPTH_TARGET;
      } else {
        depthTarget += cmdHeave * DEPTH_TARGET_SLEW;
        depthTarget = constrain(depthTarget, -30.f, 0.f);
      }

      float rollSetDeg = failsafe ? 0.0f : cmdRoll * 30.f;
      float pitchSetDeg = failsafe ? 0.0f : cmdPitch * 30.f;
      float yawRateSet = failsafe ? 0.0f : cmdYaw * 120.f; // deg/s target

      float rollErrDeg = rollSetDeg - rollDeg;
      float pitchErrDeg = pitchSetDeg - pitchDeg;
      float depthErrM = depthTarget - depthM;
      float yawRateErr = yawRateSet - imuGyrZ;

      float servoAvgDeg = 0.5f * (servoFrontDeg + servoRearDeg);
      float depthGate = depthAxisFactor(servoAvgDeg);
      float yawGate = yawAxisFactor(servoAvgDeg);
      if (mode == PITCH_STAB) {
        // In PITCH_STAB, gating is handled at the mixer (rear servo angle).
        depthGate = 1.0f;
        yawGate = 1.0f;
      }

      float rollPid = pidRoll.update(rollSetDeg, rollDeg, dt);
      float pitchPid = pidPitch.update(pitchSetDeg, pitchDeg, dt);
      float depthPid = pidDepth.update(depthTarget, depthM, dt);
      float yawPid = pidYaw.update(yawRateSet, imuGyrZ, dt);

      // Priority in stabilizer modes:
      // 1) user input, 2) large-error correction, 3) small correction, 4) ignore tiny errors.
      rollCorr  = stabilizerCorrection(rollPid, fabsf(rollErrDeg), STAB_ROLL_DB_DEG, STAB_ROLL_HIGH_DEG);
      pitchCorr = stabilizerCorrection(pitchPid, fabsf(pitchErrDeg), STAB_PITCH_DB_DEG, STAB_PITCH_HIGH_DEG);
      depthCorr = stabilizerCorrection(depthPid, fabsf(depthErrM), STAB_DEPTH_DB_M, STAB_DEPTH_HIGH_M) * depthGate;
      yawCorr   = stabilizerCorrection(yawPid, fabsf(yawRateErr), STAB_YAWRATE_DB_DPS, STAB_YAWRATE_HIGH_DPS) * yawGate;

      // Keep pilot authority highest by adding corrections on top of stick commands.
      if (failsafe && !depthOk) {
        heave = FAILSAFE_HEAVE_CMD;
      } else {
        heave = constrain(cmdHeave + depthCorr, -1.0f, 1.0f);
      }
      yaw   = failsafe ? 0.0f : constrain(cmdYaw + yawCorr, -1.0f, 1.0f);
    }

    float H1 = MIX_SURGE[0] * surge + MIX_SWAY[0] * sway + MIX_YAW[0] * yaw;
    float H2 = MIX_SURGE[1] * surge + MIX_SWAY[1] * sway + MIX_YAW[1] * yaw;
    float H3 = MIX_SURGE[2] * surge + MIX_SWAY[2] * sway + MIX_YAW[2] * yaw;
    float H4 = MIX_SURGE[3] * surge + MIX_SWAY[3] * sway + MIX_YAW[3] * yaw;

    float V1, V2, V3, V4;
    if (mode == PITCH_STAB) {
      float rearVert = depthAxisFactor(servoRearDeg);
      float rearHoriz = yawAxisFactor(servoRearDeg);
      // Gate horizontal commands for rear thrusters only.
      H3 *= rearHoriz;
      H4 *= rearHoriz;

      float heaveFront = heave * rearVert;
      float rollFront = rollCorr * rearVert;
      float heaveRear  = heave * rearVert;
      float rollRear  = rollCorr * rearVert;
      float pitchFront = pitchCorr;

      V1 = heaveFront + MIX_ROLL[0] * rollFront + MIX_PITCH[0] * pitchFront;
      V2 = heaveFront + MIX_ROLL[1] * rollFront + MIX_PITCH[1] * pitchFront;
      V3 = heaveRear  + MIX_ROLL[2] * rollRear; // no pitch on rear
      V4 = heaveRear  + MIX_ROLL[3] * rollRear; // no pitch on rear
    } else {
      V1 = heave + MIX_ROLL[0] * rollCorr + MIX_PITCH[0] * pitchCorr;
      V2 = heave + MIX_ROLL[1] * rollCorr + MIX_PITCH[1] * pitchCorr;
      V3 = heave + MIX_ROLL[2] * rollCorr + MIX_PITCH[2] * pitchCorr;
      V4 = heave + MIX_ROLL[3] * rollCorr + MIX_PITCH[3] * pitchCorr;
    }

    float T1, T2, T3, T4;
    if (mode == MANUAL) {
      // MANUAL: ESC mix is independent from servo angle.
      T1 = H1 + V1;
      T2 = H2 + V2;
      T3 = H3 + V3;
      T4 = H4 + V4;
    } else {
      // DEPTH_STAB: use servo tilt for horizontal/vertical projection.
      // Mechanical reference:
      // 45 deg  -> fully forward (horizontal)
      // 135 deg -> fully up (vertical)
      float tiltFront = (servoFrontDeg - SERVO_DEG_FORWARD) * DEG_TO_RAD;
      float tiltRear  = (servoRearDeg  - SERVO_DEG_FORWARD) * DEG_TO_RAD;
      float ctFront = cosf(tiltFront);
      float stFront = sinf(tiltFront);
      float ctRear  = cosf(tiltRear);
      float stRear  = sinf(tiltRear);

      T1 = H1 * ctFront + V1 * stFront;
      T2 = H2 * ctFront + V2 * stFront;
      T3 = H3 * ctRear  + V3 * stRear;
      T4 = H4 * ctRear  + V4 * stRear;
    }

    float maxAbs = max(max(fabs(T1), fabs(T2)), max(fabs(T3), fabs(T4)));
    if (maxAbs > 1.0f) { T1/=maxAbs; T2/=maxAbs; T3/=maxAbs; T4/=maxAbs; }

    if (!armed) T1=T2=T3=T4=0;

    sendEscs(T1,T2,T3,T4);
    writeServo(servoFront, servoFrontDeg);
    writeServo(servoRear,  servoRearDeg);
    updateStatusLed(armed, mode, rcLost, imuOk, lowBattery);

    // -------- Debug print every 200 ms --------
    if (millis() - lastDebug >= 200) {
      lastDebug = millis();
      printIbusChannels();
    }

    vTaskDelay(pdMS_TO_TICKS(2)); // ~400 Hz
  }
}

// ---------- Telemetry (Core 1) ----------
void telemetryLoop(void*) {
  esp_task_wdt_add(NULL);
  for (;;) {
    feedWatchdog();
    vTaskDelay(pdMS_TO_TICKS(20)); // kept minimal; main debug in controlLoop
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  pixel.begin();
  pixel.setBrightness(40);
  pixel.show();
  DBG_PRINTLN("BOOT: setup start");
  pinMode(PIN_CAL_BUTTON, INPUT_PULLUP);
  initWatchdog();
  initPreferences();
  loadLevelCalibration();
  DBG_PRINTLN("BOOT: calibration loaded");

  ibus.begin(Serial1, IBUSBM_NOTIMER, PIN_IBUS_RX, PIN_IBUS_TX);
  for (int i = 0; i < 14; i++) rcRaw[i] = 1500; // neutral defaults until first iBus frame
  DBG_PRINTLN("BOOT: iBus init done");

  imuOk = initIMU();
  DBG_PRINTF("BOOT: IMU init %s\n", imuOk ? "OK" : "FAILED");
  depthOk = initDepth();
  DBG_PRINTF("BOOT: Depth init %s\n", depthOk ? "OK" : "FAILED");

  if (IBUS_DEBUG_ONLY) {
    DBG_PRINTLN("BOOT: iBus debug-only mode");
    return;
  }

  DBG_PRINTLN("BOOT: PWM timer alloc start");
  ESP32PWM::allocateTimer(0);
  DBG_PRINTLN("BOOT: timer 0 ok");
  ESP32PWM::allocateTimer(1);
  DBG_PRINTLN("BOOT: timer 1 ok");
  ESP32PWM::allocateTimer(2);
  DBG_PRINTLN("BOOT: timer 2 ok");
  ESP32PWM::allocateTimer(3);
  DBG_PRINTLN("BOOT: timer 3 ok");

  DBG_PRINTLN("BOOT: ESC period set");
  esc1.setPeriodHertz(50); esc2.setPeriodHertz(50);
  esc3.setPeriodHertz(50); esc4.setPeriodHertz(50);
  int escCh1 = esc1.attach(PIN_ESC1, ESC_US_REV, ESC_US_FWD);
  DBG_PRINTF("BOOT: ESC1 attach ch=%d pin=%d\n", escCh1, PIN_ESC1);
  int escCh2 = esc2.attach(PIN_ESC2, ESC_US_REV, ESC_US_FWD);
  DBG_PRINTF("BOOT: ESC2 attach ch=%d pin=%d\n", escCh2, PIN_ESC2);
  int escCh3 = esc3.attach(PIN_ESC3, ESC_US_REV, ESC_US_FWD);
  DBG_PRINTF("BOOT: ESC3 attach ch=%d pin=%d\n", escCh3, PIN_ESC3);
  int escCh4 = esc4.attach(PIN_ESC4, ESC_US_REV, ESC_US_FWD);
  DBG_PRINTF("BOOT: ESC4 attach ch=%d pin=%d\n", escCh4, PIN_ESC4);
  sendEscs(0, 0, 0, 0); // neutral

  DBG_PRINTLN("BOOT: servo period set");
  servoFront.setPeriodHertz(50);
  servoRear.setPeriodHertz(50);
  int srvChFront = servoFront.attach(PIN_SERVO_FRONT, 500, 2500);
  DBG_PRINTF("BOOT: ServoFront attach ch=%d pin=%d\n", srvChFront, PIN_SERVO_FRONT);
  int srvChRear = servoRear.attach(PIN_SERVO_REAR, 500, 2500);
  DBG_PRINTF("BOOT: ServoRear attach ch=%d pin=%d\n", srvChRear, PIN_SERVO_REAR);
  DBG_PRINTLN("BOOT: PWM attach done");

  BaseType_t controlTaskOk = xTaskCreatePinnedToCore(controlLoop, "control", 8192, nullptr, 3, nullptr, 0);
  DBG_PRINTF("BOOT: control task create=%d\n", int(controlTaskOk));
  BaseType_t telemetryTaskOk = xTaskCreatePinnedToCore(telemetryLoop, "telemetry", 4096, nullptr, 1, nullptr, 1);
  DBG_PRINTF("BOOT: telemetry task create=%d\n", int(telemetryTaskOk));
  DBG_PRINTLN("BOOT: tasks started");
}

void loop() {
  if (IBUS_DEBUG_ONLY) {
    static uint32_t lastIbusPrint = 0;
    feedWatchdog();
    ibusTask();
    bool rcLost = (micros() - lastRcMicros) > RC_LOST_US;
    bool failsafe = rcLost;
    int modePwm = rcRaw[RC_CH_MODE];
    if (failsafe) {
      mode = DEPTH_STAB;
    } else if (modePwm < 1300) {
      mode = MANUAL;
    } else if (modePwm < 1700) {
      mode = DEPTH_STAB;
    } else {
      mode = PITCH_STAB;
    }

    int armPwm = rcRaw[RC_CH_ARM];
    if (!failsafe) {
      if (armPwm > 1700) {
        armed = true;
      } else if (armPwm < 1300) {
        armed = false;
      }
    }

    static uint32_t lastImuMicros = micros();
    uint32_t nowUs = micros();
    float dt = (nowUs - lastImuMicros) * 1e-6f;
    lastImuMicros = nowUs;
    dt = constrain(dt, 0.001f, 0.05f);
    updateImuAndDepth(dt);
    if (HAVE_BATT_SENSE) {
      batteryVolts = readBatteryVolts();
      lowBattery = batteryVolts > 0.1f && batteryVolts < BATT_LOW_VOLTS;
    } else {
      batteryVolts = 0.0f;
      lowBattery = false;
    }
    if (HAVE_CURR_SENSE) {
      currentAmps = readCurrentAmps();
    } else {
      currentAmps = 0.0f;
    }
    updateStatusLed(armed, mode, rcLost, imuOk, lowBattery);
    if (millis() - lastIbusPrint >= 200) {
      lastIbusPrint = millis();
      printIbusChannels();
    }
    delay(2);
  } else {
    // unused in full mode (tasks run on both cores)
  }
}