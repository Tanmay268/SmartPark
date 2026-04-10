import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from threading import Lock

import cv2
import easyocr
import numpy as np

from .database import vehicle_info_for_plate
from .settings import BASE_DIR, CAMERA_INDEX, EASY_OCR_LANG, GATE_CAMERA_CAPTURE_COMMAND, GATE_CAMERA_IMAGE_PATH, GATE_CAPTURE_DIR, GATE_MOCK_PLATE, MANUAL_OCR_DIR, SHOW_CAMERA_PREVIEW

_reader = None
_reader_lock = Lock()
logger = logging.getLogger(__name__)


def get_reader():
    global _reader
    with _reader_lock:
        if _reader is None:
            _reader = easyocr.Reader([EASY_OCR_LANG], gpu=False)
        return _reader


def build_capture_path(prefix: str = 'gate') -> Path:
    return GATE_CAPTURE_DIR / f'{prefix}_{int(time.time() * 1000)}.jpg'


def create_mock_capture() -> Path:
    output = build_capture_path('mock')
    canvas = np.full((320, 640, 3), 240, dtype=np.uint8)
    cv2.putText(canvas, 'SMART PARKING MOCK', (70, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (40, 40, 40), 2, cv2.LINE_AA)
    cv2.putText(canvas, GATE_MOCK_PLATE or 'MOCK1234', (120, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (20, 20, 20), 3, cv2.LINE_AA)
    cv2.imwrite(str(output), canvas)
    return output


def _open_available_camera():
    candidates = [CAMERA_INDEX]
    for fallback_index in (0, 1, 2):
        if fallback_index not in candidates:
            candidates.append(fallback_index)

    for camera_index in candidates:
        logger.info('Trying laptop camera index %s', camera_index)
        for backend_name, backend_flag in (
            ('DirectShow', cv2.CAP_DSHOW),
            ('MediaFoundation', cv2.CAP_MSMF),
            ('Default', None),
        ):
            camera = cv2.VideoCapture(camera_index, backend_flag) if backend_flag is not None else cv2.VideoCapture(camera_index)
            if camera.isOpened():
                logger.info('Using laptop camera index %s via %s for automatic capture', camera_index, backend_name)
                return camera, camera_index
            camera.release()

    raise RuntimeError('Could not open any laptop camera. Check webcam permissions or set CAMERA_INDEX in backend/.env')


def capture_gate_image() -> Path:
    output = build_capture_path()
    if GATE_CAMERA_IMAGE_PATH:
        logger.info('Capturing gate image using fixed image path: %s', GATE_CAMERA_IMAGE_PATH)
        shutil.copyfile(GATE_CAMERA_IMAGE_PATH, output)
        return output
    command_error = None
    if GATE_CAMERA_CAPTURE_COMMAND:
        command = GATE_CAMERA_CAPTURE_COMMAND.replace('{output}', str(output))
        logger.info('Capturing gate image using shell command')
        executable = command.split(' ', 1)[0].strip('"')
        try:
            if os.path.isabs(executable) and not Path(executable).exists():
                command_error = f'Camera capture command executable not found: {executable}'
                logger.warning(command_error)
            else:
                result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=20)
                if result.returncode == 0 and output.exists():
                    return output
                command_error = result.stderr.strip() or result.stdout.strip() or 'Camera capture command failed'
                logger.warning('Shell camera capture failed: %s', command_error)
        except OSError as exc:
            command_error = f'Camera capture command could not be accessed: {exc}'
            logger.warning(command_error)
    if GATE_MOCK_PLATE:
        logger.info('Capturing gate image using mock plate mode')
        return create_mock_capture()

    logger.info('Capturing gate image automatically using the laptop camera')
    try:
        camera, active_index = _open_available_camera()
    except Exception as exc:
        if command_error:
            raise RuntimeError(f'{command_error}. Fallback camera also failed: {exc}') from exc
        raise
    frame = None
    for _ in range(12):
        ok, candidate = camera.read()
        if ok:
            frame = candidate
        time.sleep(0.08)
    camera.release()
    if frame is None:
        if command_error:
            raise RuntimeError(f'{command_error}. Fallback camera opened on index {active_index} but no frame was captured.')
        raise RuntimeError(f'Could not read frame from laptop camera index {active_index}')
    cv2.imwrite(str(output), frame)
    logger.info('Automatic laptop camera capture saved to %s', output)
    if SHOW_CAMERA_PREVIEW:
        cv2.imshow('Smart Parking Camera', frame)
        cv2.waitKey(700)
        cv2.destroyAllWindows()
    return output


def save_manual_upload(file_storage) -> Path:
    suffix = Path(file_storage.filename or 'upload.jpg').suffix or '.jpg'
    path = MANUAL_OCR_DIR / f'plate_{int(time.time() * 1000)}{suffix}'
    file_storage.save(path)
    return path


def run_ocr(image_path: Path) -> dict:
    if GATE_MOCK_PLATE:
        info = vehicle_info_for_plate(GATE_MOCK_PLATE)
        logger.info('Running OCR in mock mode for plate %s', info['plate'])
        return {'plate': info['plate'], 'vehicle': info['vehicle'], 'rawText': info['plate'], 'mode': 'mock-env'}
    logger.info('Running OCR on image %s', image_path)
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError('Image could not be read for OCR')
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    filtered = cv2.bilateralFilter(gray, 11, 17, 17)
    candidates = []
    raw_parts = []
    for frame in (filtered, image):
        for _, text, confidence in get_reader().readtext(frame):
            raw_parts.append(text)
            cleaned = ''.join(ch for ch in text.upper() if ch.isalnum())
            if len(cleaned) >= 6:
                candidates.append((cleaned, confidence))
        if candidates:
            break
    candidates.sort(key=lambda item: item[1], reverse=True)
    plate = candidates[0][0] if candidates else 'UNKNOWN'
    info = vehicle_info_for_plate(plate)
    logger.info('OCR result plate=%s raw=%s', info['plate'] or plate, ' '.join(part for part in raw_parts if part).strip() or plate)
    return {'plate': info['plate'] or plate, 'vehicle': info['vehicle'], 'rawText': ' '.join(part for part in raw_parts if part).strip() or plate, 'mode': 'easyocr'}


def relative_upload_path(path: Path) -> str:
    return str(path.relative_to(BASE_DIR)).replace('\\', '/')
