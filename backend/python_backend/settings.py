import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent / '.env'


def load_env_file() -> None:
    if not ENV_PATH.exists():
        return
    for raw_line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()

DATA_DIR = BASE_DIR / 'data'
UPLOAD_DIR = BASE_DIR / 'uploads'
GATE_CAPTURE_DIR = UPLOAD_DIR / 'gate-captures'
MANUAL_OCR_DIR = UPLOAD_DIR / 'manual-ocr'
DB_PATH = DATA_DIR / 'smart_parking.db'

for folder in (DATA_DIR, UPLOAD_DIR, GATE_CAPTURE_DIR, MANUAL_OCR_DIR):
    folder.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.getenv('SECRET_KEY', 'smart-parking-python-secret')
TOKEN_TTL_HOURS = int(os.getenv('TOKEN_TTL_HOURS', '168'))
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3000')
SERIAL_PORT = os.getenv('SERIAL_PORT', 'COM3')
SERIAL_BAUD = int(os.getenv('SERIAL_BAUD', '9600'))
SIMULATION_MODE = os.getenv('SIMULATION_MODE', 'false').lower() == 'true'
SERIAL_LISTENER_ENABLED = os.getenv('SERIAL_LISTENER_ENABLED', 'true').lower() == 'true'
CAMERA_INDEX = int(os.getenv('CAMERA_INDEX', '0'))
AUTO_BOOKING_DURATION_HOURS = int(os.getenv('AUTO_BOOKING_DURATION_HOURS', '2'))
GATE_ENTRY_DEBOUNCE_MS = int(os.getenv('GATE_ENTRY_DEBOUNCE_MS', '5000'))
SYSTEM_AUTOMATION_EMAIL = os.getenv('SYSTEM_AUTOMATION_EMAIL', 'gate-automation@parking.local')
GATE_MOCK_PLATE = os.getenv('GATE_MOCK_PLATE', '').strip().upper()
GATE_CAMERA_IMAGE_PATH = os.getenv('GATE_CAMERA_IMAGE_PATH', '').strip()
GATE_CAMERA_CAPTURE_COMMAND = os.getenv('GATE_CAMERA_CAPTURE_COMMAND', '').strip()
SHOW_CAMERA_PREVIEW = os.getenv('SHOW_CAMERA_PREVIEW', 'false').lower() == 'true'
EASY_OCR_LANG = os.getenv('EASY_OCR_LANG', 'en')
CONSOLE_PAYMENT_PROMPT_ENABLED = os.getenv('CONSOLE_PAYMENT_PROMPT_ENABLED', 'false').lower() == 'true'
