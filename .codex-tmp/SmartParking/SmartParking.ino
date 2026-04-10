/**
 * SmartParking.ino
 * Arduino Nano - Smart Parking System
 *
 * Current hardware:
 *   1 x IR sensor at entry gate -> D2
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

#define IR_GATE 2
#define SERVO_PIN 9
#define LCD_ADDRESS 0x27
#define IR_USE_INTERNAL_PULLUP false

const bool IR_AUTO_CALIBRATE = true;
const int IR_DEFAULT_IDLE_STATE = HIGH;
const unsigned long SENSOR_CALIBRATION_MS = 1500;

Servo gateServo;
LiquidCrystal_I2C lcd(LCD_ADDRESS, 16, 2);

bool gateObjectDetected = false;
bool gateOpen = false;
bool triggerLatched = false;
bool sensorCalibrated = false;
unsigned long gateOpenTime = 0;
const unsigned long GATE_HOLD_MS = 5000;
const unsigned long SENSOR_DEBOUNCE_MS = 120;
const unsigned long SENSOR_ARM_DELAY_MS = 3000;
const unsigned long SENSOR_CLEAR_REARM_MS = 1500;
unsigned long sensorArmedAt = 0;
unsigned long sensorCalibrationStartedAt = 0;
unsigned long lastClearAt = 0;
unsigned long sensorActiveSince = 0;
unsigned long sensorIdleSince = 0;
unsigned long rawChangedAt = 0;
int lastRawPinValue = IR_DEFAULT_IDLE_STATE;
int stablePinValue = IR_DEFAULT_IDLE_STATE;
int sensorIdleState = IR_DEFAULT_IDLE_STATE;
int sensorActiveState = IR_DEFAULT_IDLE_STATE == HIGH ? LOW : HIGH;
unsigned int calibrationHighSamples = 0;
unsigned int calibrationLowSamples = 0;
String lcdLineCache[2] = {"", ""};

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
  stablePinValue = lastRawPinValue;
  gateObjectDetected = false;
  sensorActiveSince = 0;
  sensorIdleSince = millis();

  Serial.print("IR_IDLE_STATE:");
  Serial.println(sensorIdleState == LOW ? "LOW" : "HIGH");
  Serial.print("IR_ACTIVE_STATE:");
  Serial.println(sensorActiveState == LOW ? "LOW" : "HIGH");
  Serial.print("IR_ARM_DELAY_MS:");
  Serial.println(SENSOR_ARM_DELAY_MS);
  Serial.println("ARDUINO_READY");
}

void readGateSensor() {
  int pinValue = digitalRead(IR_GATE);
  if (pinValue != lastRawPinValue) {
    lastRawPinValue = pinValue;
    rawChangedAt = millis();
  }

  if (millis() - rawChangedAt < SENSOR_DEBOUNCE_MS) {
    return;
  }

  if (stablePinValue != lastRawPinValue) {
    stablePinValue = lastRawPinValue;
    Serial.print("IR_PIN:");
    Serial.println(stablePinValue == HIGH ? "HIGH" : "LOW");
  }

  if (!sensorCalibrated) {
    if (stablePinValue == HIGH) {
      calibrationHighSamples++;
    } else {
      calibrationLowSamples++;
    }
    if (millis() - sensorCalibrationStartedAt >= SENSOR_CALIBRATION_MS) {
      finalizeSensorCalibration();
    }
    return;
  }

  if (stablePinValue == sensorActiveState) {
    sensorIdleSince = 0;
    if (sensorActiveSince == 0) {
      sensorActiveSince = millis();
    }
    if (millis() - sensorActiveSince >= SENSOR_DEBOUNCE_MS) {
      gateObjectDetected = true;
    }
  } else {
    sensorActiveSince = 0;
    if (sensorIdleSince == 0) {
      sensorIdleSince = millis();
    }
    gateObjectDetected = false;
  }
}

void reportGateEntry() {
  if (millis() < sensorArmedAt) {
    return;
  }

  if (!gateObjectDetected) {
    if (triggerLatched && lastClearAt == 0) {
      lastClearAt = millis();
      Serial.println("IR_STATUS:IDLE_DETECTED");
      Serial.println("IR_STATUS:CLEARED");
    }
    if (triggerLatched && lastClearAt > 0 && sensorIdleSince > 0 && (millis() - sensorIdleSince >= SENSOR_CLEAR_REARM_MS)) {
      triggerLatched = false;
      Serial.println("IR_STATUS:REARMED");
      showIdleScreen();
    } else if (!triggerLatched && !gateOpen) {
      showIdleScreen();
    }
  }

  if (gateObjectDetected && !triggerLatched) {
    triggerLatched = true;
    lastClearAt = 0;
    sensorIdleSince = 0;
    Serial.println("GATE_IR_DETECTED");
    Serial.println("IR_STATUS:OBJECT_PRESENT");
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

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(25);
  pinMode(IR_GATE, IR_USE_INTERNAL_PULLUP ? INPUT_PULLUP : INPUT);
  lastRawPinValue = digitalRead(IR_GATE);
  stablePinValue = lastRawPinValue;
  rawChangedAt = millis();
  sensorCalibrationStartedAt = millis();

  gateServo.attach(SERVO_PIN);
  gateServo.write(0);

  lcd.init();
  lcd.backlight();
  showMessage("Smart Parking   ", "System Ready    ", true);
  delay(1200);
  showIdleScreen();
  sensorArmedAt = millis() + SENSOR_ARM_DELAY_MS;
  Serial.print("IR_PIN_MODE:");
  Serial.println(IR_USE_INTERNAL_PULLUP ? "INPUT_PULLUP" : "INPUT");
}

void loop() {
  readGateSensor();
  reportGateEntry();
  handleSerialCommand();
  checkGateTimeout();
  delay(50);
}
