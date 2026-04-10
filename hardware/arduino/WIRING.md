# Arduino Wiring Guide

Current project wiring uses one IR sensor at the entry gate and one IR sensor at the exit gate. Either sensor tells the Python backend when a vehicle is present, the backend runs camera capture and OCR, then sends LCD and gate commands back to Arduino.

## Components
| Component | Quantity | Notes |
|---|---:|---|
| Arduino Nano | 1 | ATmega328P |
| IR obstacle sensor | 2 | FC-51 or similar, digital output |
| SG90 servo motor | 1 | Gate open/close |
| 16x2 I2C LCD | 1 | Usually address `0x27` |
| USB cable | 1 | Arduino to laptop/backend PC |
| Jumper wires | As needed | |

## Pin Map
| Device | Device Pin | Arduino Pin |
|---|---|---|
| IR sensor | `VCC` | `5V` |
| IR sensor | `GND` | `GND` |
| Entry IR sensor | `OUT` | `D2` |
| Exit IR sensor | `OUT` | `D3` |
| Servo | `Signal` | `D9` |
| Servo | `VCC` | `5V` |
| Servo | `GND` | `GND` |
| LCD I2C | `VCC` | `5V` |
| LCD I2C | `GND` | `GND` |
| LCD I2C | `SDA` | `A4` |
| LCD I2C | `SCL` | `A5` |

## IR Sensor Behavior
- The current sketch uses `INPUT` on `D2` because most FC-51 style modules drive the output pin actively.
- During boot the sketch auto-calibrates the IR sensor for about `1.5s`, then waits another `3s` before it will trigger the gate event.
- Keep the sensor path clear during boot, otherwise the sketch can learn the wrong idle state.
- For most obstacle modules:
  - `LOW` means object detected
  - `HIGH` means no object detected
- Helpful serial commands in the Arduino Serial Monitor:
  - `IR_STATUS`
  - `IR_RECALIBRATE`
  - `IR_DEBUG_ON`
  - `IR_DEBUG_OFF`

## LCD Behavior
The backend updates the LCD through serial commands. Typical messages:

- `Vehicle Detected`
- `Starting Camera`
- `Plate Detected`
- `Slot Assigned`
- `Invalid Entry`
- `Gate Open`

## Gate Flow
1. Vehicle comes in front of the entry or exit IR sensor.
2. Arduino sends `GATE_ENTRY_DETECTED` or `GATE_EXIT_DETECTED` over serial.
3. Python backend receives the event on the configured `SERIAL_PORT`.
4. Backend starts camera capture and OCR.
5. If no number plate is detected, backend shows `Invalid Entry` on LCD and the gate stays closed.
6. If a valid plate is detected and a slot is free, backend shows the assigned slot on LCD and sends `GATE_OPEN`.
7. Arduino opens the servo gate and auto-closes it after the hold time.

## Serial Protocol
Baud rate: `9600`

Arduino sends:
- `ARDUINO_READY`
- `GATE_IR_DETECTED`
- `GATE_ENTRY_DETECTED`
- `GATE_EXIT_DETECTED`
- `GATE_OPENED`
- `GATE_CLOSED`

Arduino receives:
- `GATE_OPEN`
- `GATE_CLOSE`
- `LCD_TEXT|<line1>|<line2>`

## Backend Settings
Set these values in [backend/.env](d:/Downloads/smart-parking-system/smart-parking/backend/.env):

```env
SERIAL_PORT=COM5
SERIAL_BAUD=9600
SERIAL_LISTENER_ENABLED=true
SIMULATION_MODE=false
GATE_CAMERA_CAPTURE_COMMAND=
GATE_MOCK_PLATE=
CAMERA_INDEX=0
```

Notes:
- Keep the Arduino Serial Monitor closed while the Python backend is running, otherwise the backend cannot open the COM port.
- Use the Serial Monitor only when you are testing the Arduino by itself.
- If the LCD does not display anything, check the I2C address. Common values are `0x27` and `0x3F`.
- If the servo jitters or the Nano resets, power the servo from a stable external 5V supply and share GND with the Arduino.
