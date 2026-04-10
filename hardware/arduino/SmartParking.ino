/**
 * SmartParking.ino
 * Arduino Nano - Smart Parking System
 *
 * Current hardware:
 *   1 x IR sensor at entry gate -> D2
 *   1 x IR sensor at exit gate  -> D3
 *   1 x SG90 servo gate         -> D9
 *   1 x 16x2 I2C LCD            -> A4/A5
 *
 * Serial communication: 9600 baud
 *   Sends:    ARDUINO_READY, GATE_IR_DETECTED, GATE_OPENED, GATE_CLOSED
 *   Receives: GATE_OPEN | GATE_CLOSE | LCD_TEXT|<line1>|<line2>
 *
 * Flow:
 *   Vehicle detected -> send GATE_IR_DETECTED -> backend runs
 *   camera/OCR pipeline -> backend either shows Invalid Entry
 *   or assigns a slot and opens the gate.
 */

#include <Servo.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

#define IR_ENTRY 2
#define IR_EXIT 3
#define SERVO_PIN 9
#define LCD_ADDRESS 0x27
#define IR_USE_INTERNAL_PULLUP false

const bool IR_AUTO_CALIBRATE = true;
const int IR_DEFAULT_IDLE_STATE = HIGH;
const unsigned long SENSOR_CALIBRATION_MS = 1500;

Servo gateServo;
LiquidCrystal_I2C lcd(LCD_ADDRESS, 16, 2);

bool gateOpen = false;
bool sensorCalibrated = false;
unsigned long gateOpenTime = 0;
const unsigned long GATE_HOLD_MS = 5000;
const unsigned long SENSOR_DEBOUNCE_MS = 120;
const unsigned long SENSOR_ARM_DELAY_MS = 3000;
const unsigned long SENSOR_CLEAR_REARM_MS = 1500;
const unsigned long SENSOR_DEBUG_INTERVAL_MS = 300;
unsigned long sensorCalibrationStartedAt = 0;
unsigned long lastSensorDebugAt = 0;
int sensorIdleState = IR_DEFAULT_IDLE_STATE;
int sensorActiveState = IR_DEFAULT_IDLE_STATE == HIGH ? LOW : HIGH;
unsigned int calibrationHighSamples = 0;
unsigned int calibrationLowSamples = 0;
bool sensorDebugStreamEnabled = false;
String lcdLineCache[2] = {"", ""};

struct GateSensorState {
  uint8_t pin;
  const char* triggerEvent;
  const char* label;
  bool objectDetected;
  bool triggerLatched;
  unsigned long sensorArmedAt;
  unsigned long lastClearAt;
  unsigned long sensorActiveSince;
  unsigned long sensorIdleSince;
  unsigned long rawChangedAt;
  int lastRawPinValue;
  int stablePinValue;
};

GateSensorState entrySensor = {IR_ENTRY, "GATE_ENTRY_DETECTED", "ENTRY", false, false, 0, 0, 0, 0, 0, IR_DEFAULT_IDLE_STATE, IR_DEFAULT_IDLE_STATE};
GateSensorState exitSensor = {IR_EXIT, "GATE_EXIT_DETECTED", "EXIT", false, false, 0, 0, 0, 0, 0, IR_DEFAULT_IDLE_STATE, IR_DEFAULT_IDLE_STATE};

String normalizeLcdText(String text) {
  while (text.length() < 16) {
    text += " ";
  }
  return text.substring(0, 16);
}

void writeLcdLine(int row, String text) {
  text = normalizeLcdText(text);
  lcd.setCursor(0, row);
  lcd.print(text);
}

void showMessage(String line1, String line2, bool force = false) {
  String normalizedLine1 = normalizeLcdText(line1);
  String normalizedLine2 = normalizeLcdText(line2);

  if (!force && lcdLineCache[0] == normalizedLine1 && lcdLineCache[1] == normalizedLine2) {
    return;
  }

  if (force) {
    lcd.clear();
  }

  if (force || lcdLineCache[0] != normalizedLine1) {
    writeLcdLine(0, normalizedLine1);
    lcdLineCache[0] = normalizedLine1;
  }

  if (force || lcdLineCache[1] != normalizedLine2) {
    writeLcdLine(1, normalizedLine2);
    lcdLineCache[1] = normalizedLine2;
  }
}

void showIdleScreen() {
  showMessage(" Smart Parking  ", " Scan at Gate   ");
}

String pinStateLabel(int value) {
  return value == LOW ? "LOW" : "HIGH";
}

void printSensorStatus() {
  Serial.print("IR_STATUS|sensor=ENTRY|raw=");
  Serial.print(pinStateLabel(entrySensor.lastRawPinValue));
  Serial.print("|stable=");
  Serial.print(pinStateLabel(entrySensor.stablePinValue));
  Serial.print("|idle=");
  Serial.print(pinStateLabel(sensorIdleState));
  Serial.print("|active=");
  Serial.print(pinStateLabel(sensorActiveState));
  Serial.print("|detected=");
  Serial.print(entrySensor.objectDetected ? "YES" : "NO");
  Serial.print("|latched=");
  Serial.print(entrySensor.triggerLatched ? "YES" : "NO");
  Serial.print("|calibrated=");
  Serial.print(sensorCalibrated ? "YES" : "NO");
  Serial.print("|armed=");
  Serial.println(millis() >= entrySensor.sensorArmedAt ? "YES" : "NO");

  Serial.print("IR_STATUS|sensor=EXIT|raw=");
  Serial.print(pinStateLabel(exitSensor.lastRawPinValue));
  Serial.print("|stable=");
  Serial.print(pinStateLabel(exitSensor.stablePinValue));
  Serial.print("|idle=");
  Serial.print(pinStateLabel(sensorIdleState));
  Serial.print("|active=");
  Serial.print(pinStateLabel(sensorActiveState));
  Serial.print("|detected=");
  Serial.print(exitSensor.objectDetected ? "YES" : "NO");
  Serial.print("|latched=");
  Serial.print(exitSensor.triggerLatched ? "YES" : "NO");
  Serial.print("|calibrated=");
  Serial.print(sensorCalibrated ? "YES" : "NO");
  Serial.print("|armed=");
  Serial.println(millis() >= exitSensor.sensorArmedAt ? "YES" : "NO");
}

void resetSensorCalibration() {
  sensorCalibrated = false;
  calibrationHighSamples = 0;
  calibrationLowSamples = 0;
  sensorCalibrationStartedAt = millis();
  entrySensor.objectDetected = false;
  entrySensor.triggerLatched = false;
  entrySensor.lastClearAt = 0;
  entrySensor.sensorActiveSince = 0;
  entrySensor.sensorIdleSince = 0;
  entrySensor.sensorArmedAt = millis() + SENSOR_ARM_DELAY_MS;
  entrySensor.lastRawPinValue = digitalRead(IR_ENTRY);
  entrySensor.stablePinValue = entrySensor.lastRawPinValue;
  entrySensor.rawChangedAt = millis();

  exitSensor.objectDetected = false;
  exitSensor.triggerLatched = false;
  exitSensor.lastClearAt = 0;
  exitSensor.sensorActiveSince = 0;
  exitSensor.sensorIdleSince = 0;
  exitSensor.sensorArmedAt = millis() + SENSOR_ARM_DELAY_MS;
  exitSensor.lastRawPinValue = digitalRead(IR_EXIT);
  exitSensor.stablePinValue = exitSensor.lastRawPinValue;
  exitSensor.rawChangedAt = millis();
  Serial.println("IR_STATUS:RECALIBRATING");
  printSensorStatus();
}

void openGate() {
  gateServo.write(90);
  gateOpen = true;
  gateOpenTime = millis();
  showMessage("Gate Open       ", "Proceed Slowly  ");
  Serial.println("GATE_OPENED");
}

void closeGate() {
  gateServo.write(0);
  gateOpen = false;
  Serial.println("GATE_CLOSED");
  showIdleScreen();
}

void finalizeSensorCalibration() {
  if (!IR_AUTO_CALIBRATE) {
    sensorIdleState = IR_DEFAULT_IDLE_STATE;
  } else if (calibrationLowSamples == 0 && calibrationHighSamples == 0) {
    sensorIdleState = IR_DEFAULT_IDLE_STATE;
  } else {
    sensorIdleState = calibrationHighSamples >= calibrationLowSamples ? HIGH : LOW;
  }

  sensorActiveState = sensorIdleState == HIGH ? LOW : HIGH;
  sensorCalibrated = true;
  entrySensor.stablePinValue = entrySensor.lastRawPinValue;
  entrySensor.objectDetected = false;
  entrySensor.sensorActiveSince = 0;
  entrySensor.sensorIdleSince = millis();
  exitSensor.stablePinValue = exitSensor.lastRawPinValue;
  exitSensor.objectDetected = false;
  exitSensor.sensorActiveSince = 0;
  exitSensor.sensorIdleSince = millis();

  Serial.print("IR_IDLE_STATE:");
  Serial.println(sensorIdleState == LOW ? "LOW" : "HIGH");
  Serial.print("IR_ACTIVE_STATE:");
  Serial.println(sensorActiveState == LOW ? "LOW" : "HIGH");
  Serial.print("IR_ARM_DELAY_MS:");
  Serial.println(SENSOR_ARM_DELAY_MS);
  Serial.println("ARDUINO_READY");
}

void readGateSensor(GateSensorState &sensor) {
  int pinValue = digitalRead(sensor.pin);
  if (pinValue != sensor.lastRawPinValue) {
    sensor.lastRawPinValue = pinValue;
    sensor.rawChangedAt = millis();
  }

  if (millis() - sensor.rawChangedAt < SENSOR_DEBOUNCE_MS) {
    return;
  }

  if (sensor.stablePinValue != sensor.lastRawPinValue) {
    sensor.stablePinValue = sensor.lastRawPinValue;
    Serial.print("IR_PIN|");
    Serial.print(sensor.label);
    Serial.print("|");
    Serial.println(sensor.stablePinValue == HIGH ? "HIGH" : "LOW");
  }

  if (!sensorCalibrated) {
    if (sensor.stablePinValue == HIGH) {
      calibrationHighSamples++;
    } else {
      calibrationLowSamples++;
    }
    if (millis() - sensorCalibrationStartedAt >= SENSOR_CALIBRATION_MS) {
      finalizeSensorCalibration();
    }
    return;
  }

  if (sensor.stablePinValue == sensorActiveState) {
    sensor.sensorIdleSince = 0;
    if (sensor.sensorActiveSince == 0) {
      sensor.sensorActiveSince = millis();
    }
    if (millis() - sensor.sensorActiveSince >= SENSOR_DEBOUNCE_MS) {
      sensor.objectDetected = true;
    }
  } else {
    sensor.sensorActiveSince = 0;
    if (sensor.sensorIdleSince == 0) {
      sensor.sensorIdleSince = millis();
    }
    sensor.objectDetected = false;
  }
}

void reportGateEvent(GateSensorState &sensor) {
  if (millis() < sensor.sensorArmedAt) {
    return;
  }

  if (!sensor.objectDetected) {
    if (sensor.triggerLatched && sensor.lastClearAt == 0) {
      sensor.lastClearAt = millis();
      Serial.print("IR_STATUS:");
      Serial.print(sensor.label);
      Serial.println(":IDLE_DETECTED");
      Serial.print("IR_STATUS:");
      Serial.print(sensor.label);
      Serial.println(":CLEARED");
    }
    if (sensor.triggerLatched && sensor.lastClearAt > 0 && sensor.sensorIdleSince > 0 && (millis() - sensor.sensorIdleSince >= SENSOR_CLEAR_REARM_MS)) {
      sensor.triggerLatched = false;
      Serial.print("IR_STATUS:");
      Serial.print(sensor.label);
      Serial.println(":REARMED");
      Serial.println("IR_STATUS:REARMED");
      showIdleScreen();
    } else if (!sensor.triggerLatched && !gateOpen) {
      showIdleScreen();
    }
  }

  if (sensor.objectDetected && !sensor.triggerLatched) {
    sensor.triggerLatched = true;
    sensor.lastClearAt = 0;
    sensor.sensorIdleSince = 0;
    Serial.println(sensor.triggerEvent);
    Serial.print("IR_STATUS:");
    Serial.print(sensor.label);
    Serial.println(":OBJECT_PRESENT");
    if (sensor.pin == IR_ENTRY) {
      Serial.println("GATE_IR_DETECTED");
      Serial.println("IR_STATUS:OBJECT_PRESENT");
    }
    showMessage("Vehicle Detected", "Starting Scan   ");
  }
}

void handleSerialCommand() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    String upperCmd = cmd;
    upperCmd.toUpperCase();

    if (upperCmd == "GATE_OPEN") {
      Serial.println("SERVO_OPEN_COMMAND_RECEIVED");
      openGate();
    } else if (upperCmd == "GATE_CLOSE") {
      Serial.println("SERVO_CLOSE_COMMAND_RECEIVED");
      closeGate();
    } else if (upperCmd == "IR_STATUS") {
      printSensorStatus();
    } else if (upperCmd == "IR_RECALIBRATE") {
      resetSensorCalibration();
    } else if (upperCmd == "IR_DEBUG_ON") {
      sensorDebugStreamEnabled = true;
      Serial.println("IR_DEBUG:ON");
      printSensorStatus();
    } else if (upperCmd == "IR_DEBUG_OFF") {
      sensorDebugStreamEnabled = false;
      Serial.println("IR_DEBUG:OFF");
    } else if (upperCmd.startsWith("LCD_TEXT|")) {
      int firstSep = cmd.indexOf('|');
      int secondSep = cmd.indexOf('|', firstSep + 1);
      if (firstSep >= 0 && secondSep >= 0) {
        String line1 = cmd.substring(firstSep + 1, secondSep);
        String line2 = cmd.substring(secondSep + 1);
        showMessage(line1, line2);
      }
    }
  }
}

void checkGateTimeout() {
  if (gateOpen && (millis() - gateOpenTime >= GATE_HOLD_MS)) {
    closeGate();
  }
}

void streamSensorDebug() {
  if (!sensorDebugStreamEnabled) {
    return;
  }

  if (millis() - lastSensorDebugAt < SENSOR_DEBUG_INTERVAL_MS) {
    return;
  }

  lastSensorDebugAt = millis();
  printSensorStatus();
}

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(25);
  pinMode(IR_ENTRY, IR_USE_INTERNAL_PULLUP ? INPUT_PULLUP : INPUT);
  pinMode(IR_EXIT, IR_USE_INTERNAL_PULLUP ? INPUT_PULLUP : INPUT);
  entrySensor.lastRawPinValue = digitalRead(IR_ENTRY);
  entrySensor.stablePinValue = entrySensor.lastRawPinValue;
  entrySensor.rawChangedAt = millis();
  exitSensor.lastRawPinValue = digitalRead(IR_EXIT);
  exitSensor.stablePinValue = exitSensor.lastRawPinValue;
  exitSensor.rawChangedAt = millis();
  sensorCalibrationStartedAt = millis();

  gateServo.attach(SERVO_PIN);
  gateServo.write(0);

  lcd.init();
  lcd.backlight();
  showMessage("Smart Parking   ", "System Ready    ", true);
  delay(1200);
  showIdleScreen();
  entrySensor.sensorArmedAt = millis() + SENSOR_ARM_DELAY_MS;
  exitSensor.sensorArmedAt = millis() + SENSOR_ARM_DELAY_MS;
  Serial.print("IR_PIN_MODE:");
  Serial.println(IR_USE_INTERNAL_PULLUP ? "INPUT_PULLUP" : "INPUT");
  Serial.println("IR_COMMANDS:IR_STATUS,IR_RECALIBRATE,IR_DEBUG_ON,IR_DEBUG_OFF");
  Serial.println("IR_HINT:KEEP_PATH_CLEAR_DURING_BOOT");
}

void loop() {
  readGateSensor(entrySensor);
  readGateSensor(exitSensor);
  reportGateEvent(entrySensor);
  reportGateEvent(exitSensor);
  handleSerialCommand();
  checkGateTimeout();
  streamSensorDebug();
  delay(50);
}
