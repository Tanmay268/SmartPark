# Smart Parking Hardware Debug Setup

Use this in order. Do not test everything at once.

## 1. Prepare the bench

1. Disconnect the servo horn from the gate arm if it can jam.
2. Keep only one USB connection to the Arduino.
3. Close the Python backend and close Arduino Serial Monitor before any PC-side serial test.
4. Keep the IR sensor path clear before powering the Arduino.

## 2. Prove the Nano is alive

Expected:
- LCD turns on
- Servo moves to closed position
- Serial prints `IR_PIN_MODE`, then `ARDUINO_READY`

If not:
- No power: check USB cable and Nano port
- No LCD: check `0x27` vs `0x3F`
- Constant resets: servo power is pulling the Nano down

## 3. Test the IR sensor alone

Wire only:
- IR `VCC` -> `5V`
- IR `GND` -> `GND`
- IR `OUT` -> `D2`

Open Arduino Serial Monitor:
- baud `9600`
- line ending `Newline`

Commands:
- `IR_STATUS`
- `IR_DEBUG_ON`

Expected with no object:
- `raw` and `stable` should settle
- `calibrated=YES`
- `armed=YES` after about 3 seconds
- `detected=NO`

Move your hand or a car model in front of the sensor:
- `IR_PIN:LOW` or `IR_PIN:HIGH` should change at least once
- then `GATE_IR_DETECTED`
- then `IR_STATUS:OBJECT_PRESENT`

Remove the object:
- `IR_STATUS:CLEARED`
- then `IR_STATUS:REARMED`

If the IR sensor does nothing:
- adjust the sensor potentiometer
- confirm the module LED changes when an object passes
- send `IR_RECALIBRATE` with the path clear
- if the board LED changes but serial never changes, recheck `OUT -> D2`
- if serial flips randomly with no object, check GND and power noise

## 4. Test the servo alone

Keep the IR sensor connected or disconnected, either is fine.

From Serial Monitor send:
- `GATE_OPEN`
- `GATE_CLOSE`

Expected:
- servo moves about 90 degrees open
- servo returns closed
- serial prints `GATE_OPENED` and `GATE_CLOSED`

If not:
- brownout/reset means servo needs separate 5V supply
- always share ground between supply and Arduino

## 5. Test the LCD alone

From Serial Monitor send:

`LCD_TEXT|IR sensor ok    |Next: backend    `

Expected:
- both lines update cleanly

If not:
- check SDA/SCL on `A4/A5`
- test I2C address

## 6. Test PC serial link only

1. Close Serial Monitor.
2. Set the right `SERIAL_PORT` in `backend/.env`.
3. Keep `SIMULATION_MODE=false`.
4. Run:

```powershell
cd backend
py -3.11 debug_hardware.py
```

Expected:
- port opens
- LCD debug text appears
- IR trigger is received in the terminal

If it says access denied:
- backend app or Serial Monitor still owns the COM port

## 7. Test camera and OCR separately

Still with backend only:

```powershell
cd backend
py -3.11 debug_hardware.py
```

Expected:
- image capture succeeds
- OCR returns a usable plate

If serial works but OCR fails:
- hardware is fine, camera/OCR is the next problem

## 8. Test full flow

Only after steps 2 to 7 pass:

1. Start backend
2. Start frontend if needed
3. Keep Serial Monitor closed
4. Trigger the IR sensor once

Expected order:
1. Arduino sends `GATE_IR_DETECTED`
2. backend captures image
3. backend updates LCD
4. backend sends `GATE_OPEN`
5. Arduino opens servo

## Fast fault map

- No `ARDUINO_READY`: board boot or sketch issue
- `ARDUINO_READY` but no `IR_PIN` changes: wiring, sensor trim, or dead sensor
- `IR_PIN` changes but no `GATE_IR_DETECTED`: calibration or arm timing
- `GATE_IR_DETECTED` appears in Serial Monitor but not in Python: wrong COM port or COM port busy
- Python sees trigger but gate never opens: OCR/backend decision path
