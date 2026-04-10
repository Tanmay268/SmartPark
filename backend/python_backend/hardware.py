import logging
import time
from threading import Lock

import serial

from .settings import SERIAL_BAUD, SERIAL_LISTENER_ENABLED, SERIAL_PORT, SIMULATION_MODE

_serial_lock = Lock()
_serial_connection = None
_serial_ready_at = 0.0
_sensor_trigger_armed = True
logger = logging.getLogger(__name__)
_VERBOSE_SERIAL_LINES = {
    'IR_STATUS:OBJECT_PRESENT',
    'IR_STATUS:ENTRY:OBJECT_PRESENT',
    'IR_STATUS:EXIT:OBJECT_PRESENT',
    'IR_STATUS:IDLE_DETECTED',
    'IR_STATUS:ENTRY:IDLE_DETECTED',
    'IR_STATUS:EXIT:IDLE_DETECTED',
    'IR_STATUS:CLEARED',
    'IR_STATUS:ENTRY:CLEARED',
    'IR_STATUS:EXIT:CLEARED',
    'IR_STATUS:REARMED',
    'IR_STATUS:ENTRY:REARMED',
    'IR_STATUS:EXIT:REARMED',
    'IR_PIN:LOW',
    'IR_PIN:HIGH',
    'IR_PIN|ENTRY|LOW',
    'IR_PIN|ENTRY|HIGH',
    'IR_PIN|EXIT|LOW',
    'IR_PIN|EXIT|HIGH',
    'SERVO_OPEN_COMMAND_RECEIVED',
    'SERVO_CLOSE_COMMAND_RECEIVED',
    'GATE_OPENED',
    'GATE_CLOSED',
}


def _command_summary(command: str) -> str:
    if command.startswith('LCD_TEXT|'):
        parts = command.split('|', 2)
        line1 = parts[1].rstrip() if len(parts) > 1 else ''
        line2 = parts[2].rstrip() if len(parts) > 2 else ''
        return f'LCD "{line1}" / "{line2}"'
    if command == 'GATE_OPEN':
        return 'Gate open request'
    if command == 'GATE_CLOSE':
        return 'Gate close request'
    return command


def _close_serial_connection() -> None:
    global _serial_connection
    with _serial_lock:
        if _serial_connection and _serial_connection.is_open:
            try:
                logger.warning('Closing serial connection on %s', SERIAL_PORT)
                _serial_connection.close()
            except Exception:
                pass
        _serial_connection = None


def _ensure_serial_connection():
    global _serial_connection, _serial_ready_at
    if SIMULATION_MODE or not SERIAL_LISTENER_ENABLED:
        return None
    with _serial_lock:
        if _serial_connection and _serial_connection.is_open:
            return _serial_connection
        try:
            logger.info('Opening serial connection on %s @ %s baud', SERIAL_PORT, SERIAL_BAUD)
            _serial_connection = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1, write_timeout=1)
            time.sleep(2)
            try:
                _serial_connection.reset_input_buffer()
                _serial_connection.reset_output_buffer()
            except Exception:
                pass
            _serial_ready_at = time.time() + 4
            logger.info('Serial connection ready on %s', SERIAL_PORT)
            return _serial_connection
        except Exception as exc:
            logger.error('Failed to open serial connection on %s: %s', SERIAL_PORT, exc)
            _serial_connection = None
            return None


def send_command(command: str) -> None:
    connection = _ensure_serial_connection()
    if not connection:
        logger.warning('Skipped serial command because connection is unavailable: %s', command)
        return
    with _serial_lock:
        try:
            logger.debug('Sending serial command: %s', _command_summary(command))
            connection.write(f'{command}\n'.encode('utf-8'))
            connection.flush()
        except Exception as exc:
            logger.error('Failed to send serial command %s: %s', command, exc)
            _close_serial_connection()


def update_lcd(line1: str, line2: str) -> None:
    cleaned1 = line1.replace('\r', ' ').replace('\n', ' ').replace('|', ' ')[:16].ljust(16)
    cleaned2 = line2.replace('\r', ' ').replace('\n', ' ').replace('|', ' ')[:16].ljust(16)
    send_command(f'LCD_TEXT|{cleaned1}|{cleaned2}')


def open_gate() -> None:
    send_command('GATE_OPEN')


def serial_listener_loop(handler) -> None:
    global _sensor_trigger_armed
    if SIMULATION_MODE or not SERIAL_LISTENER_ENABLED:
        logger.warning('Serial listener disabled. simulation=%s enabled=%s', SIMULATION_MODE, SERIAL_LISTENER_ENABLED)
        return
    trigger_lines = {
        'GATE_IR_DETECTED',
        'ENTRY_SENSOR_TRIGGERED',
        'GATE_ENTRY_DETECTED',
        'GATE_EXIT_DETECTED',
        'EXIT_SENSOR_TRIGGERED',
    }
    while True:
        connection = _ensure_serial_connection()
        if not connection:
            time.sleep(2)
            continue
        try:
            if connection.in_waiting:
                line = connection.readline().decode('utf-8', errors='ignore').strip().upper()
                if line:
                    if line in _VERBOSE_SERIAL_LINES:
                        logger.debug('Received serial line: %s', line)
                    else:
                        logger.info('Serial event: %s', line)
                if line == 'IR_STATUS:REARMED':
                    _sensor_trigger_armed = True
                    logger.debug('Sensor trigger rearmed')
                    continue
                if line in trigger_lines:
                    if time.time() < _serial_ready_at:
                        logger.warning('Ignoring serial trigger during startup warmup: %s', line)
                        continue
                    if not _sensor_trigger_armed:
                        logger.warning('Ignoring repeated serial trigger until sensor rearm: %s', line)
                        continue
                    _sensor_trigger_armed = False
                    logger.info('Gate trigger received: %s', line)
                    if 'EXIT' in line:
                        handler('serial-bridge-exit')
                    else:
                        handler('serial-bridge-entry')
            time.sleep(0.1)
        except Exception:
            _close_serial_connection()
            time.sleep(1)
