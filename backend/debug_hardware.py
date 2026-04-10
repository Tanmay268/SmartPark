import time

import serial

from python_backend.ocr import capture_gate_image, run_ocr
from python_backend.settings import CAMERA_INDEX, GATE_CAMERA_CAPTURE_COMMAND, GATE_CAMERA_IMAGE_PATH, GATE_MOCK_PLATE, SERIAL_BAUD, SERIAL_LISTENER_ENABLED, SERIAL_PORT, SHOW_CAMERA_PREVIEW, SIMULATION_MODE


def print_step(title: str) -> None:
    print(f'\n=== {title} ===')


def test_config() -> None:
    print_step('CONFIG')
    print(f'SERIAL_PORT={SERIAL_PORT}')
    print(f'SERIAL_BAUD={SERIAL_BAUD}')
    print(f'SERIAL_LISTENER_ENABLED={SERIAL_LISTENER_ENABLED}')
    print(f'SIMULATION_MODE={SIMULATION_MODE}')
    print(f'CAMERA_INDEX={CAMERA_INDEX}')
    print(f'GATE_CAMERA_CAPTURE_COMMAND={"set" if GATE_CAMERA_CAPTURE_COMMAND else "empty"}')
    print(f'GATE_CAMERA_IMAGE_PATH={GATE_CAMERA_IMAGE_PATH or "empty"}')
    print(f'GATE_MOCK_PLATE={GATE_MOCK_PLATE or "empty"}')
    print(f'SHOW_CAMERA_PREVIEW={SHOW_CAMERA_PREVIEW}')


def test_serial_and_listen(listen_seconds: int = 10) -> None:
    print_step('SERIAL')
    if SIMULATION_MODE or not SERIAL_LISTENER_ENABLED:
        print('SKIP: serial listener is disabled by config')
        return

    with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1, write_timeout=1) as ser:
        print(f'PASS: opened {SERIAL_PORT} at {SERIAL_BAUD} baud')
        time.sleep(2)
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
        except Exception:
            pass
        ser.write(b'IR_STATUS\n')
        ser.flush()
        time.sleep(0.3)
        ser.write(b'LCD_TEXT|Debug Mode      |IR test now      \n')
        ser.flush()
        print('PASS: sent LCD test command')
        ser.write(b'IR_DEBUG_ON\n')
        ser.flush()
        print('PASS: requested live IR debug stream')
        print(f'LISTEN: trigger the IR sensor within {listen_seconds} seconds')
        started = time.time()
        lines = []
        while time.time() - started < listen_seconds:
            if ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    lines.append(line)
                    print(f'SERIAL> {line}')
            time.sleep(0.1)
        trigger_lines = {'GATE_IR_DETECTED', 'IR_STATUS:OBJECT_PRESENT', 'ENTRY_SENSOR_TRIGGERED', 'GATE_ENTRY_DETECTED'}
        if any(line.upper() in trigger_lines for line in lines):
            print('PASS: IR trigger reached the serial port')
        else:
            print('FAIL: no IR/object trigger line was received')
            print('HINT: look for IR_STATUS|... lines first. If raw/stable never change, debug wiring and sensor trim before backend.')
        ser.write(b'IR_DEBUG_OFF\n')
        ser.flush()


def test_camera_and_ocr() -> None:
    print_step('CAMERA')
    image_path = capture_gate_image()
    file_size = image_path.stat().st_size if image_path.exists() else 0
    print(f'PASS: image captured at {image_path}')
    print(f'INFO: image size = {file_size} bytes')

    print_step('OCR')
    ocr = run_ocr(image_path)
    print(f'plate={ocr["plate"]}')
    print(f'rawText={ocr["rawText"]}')
    print(f'mode={ocr["mode"]}')
    if not ocr['plate'] or ocr['plate'] == 'UNKNOWN':
        print('FAIL: OCR did not detect a usable plate')
    else:
        print('PASS: OCR detected a plate')


def main() -> None:
    print('Smart Parking hardware debug')
    test_config()
    try:
        test_serial_and_listen()
    except Exception as exc:
        message = str(exc)
        if 'Access is denied' in message or 'PermissionError' in message:
            print(f'FAIL: serial test failed: {exc}')
            print('HINT: COM port is already in use. Stop `python app.py`, close Arduino Serial Monitor, then rerun this debug script.')
        else:
            print(f'FAIL: serial test failed: {exc}')
    try:
        test_camera_and_ocr()
    except Exception as exc:
        print(f'FAIL: camera/OCR test failed: {exc}')


if __name__ == '__main__':
    main()
