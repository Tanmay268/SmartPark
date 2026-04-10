import logging
import shutil
import time
from pathlib import Path
from threading import Lock
from typing import Optional

from .database import create_gate_scan, ensure_automation_user, update_gate_scan
from .events import emit_gate_event, emit_progress, now_iso
from .hardware import open_gate, send_command, update_lcd
from .ocr import build_capture_path, capture_gate_image, relative_upload_path, run_ocr
from .settings import AUTO_BOOKING_DURATION_HOURS, GATE_ENTRY_DEBOUNCE_MS
from .smart_backend import (
    create_smart_booking,
    fetch_booking_by_id,
    fetch_gate_booking_by_plate,
    update_smart_booking_status,
)

_scan_lock = Lock()
_last_triggered_at = 0.0
logger = logging.getLogger(__name__)


_PIPELINE_DEBUG_STEPS = {
    'capture-start',
    'capture-complete',
    'ocr-complete',
    'trigger-received',
    'new-entry',
    'exit-match',
    'booked-entry-match',
    'completed',
}


def _log_pipeline_step(scan_id: str, step: str, level: int | None = None, **details) -> None:
    payload = ' '.join(f'{key}={value}' for key, value in details.items() if value is not None and value != '')
    chosen_level = level if level is not None else (logging.DEBUG if step in _PIPELINE_DEBUG_STEPS else logging.INFO)
    logger.log(chosen_level, 'PIPELINE | scan=%s | step=%s%s%s', scan_id, step, ' | ' if payload else '', payload)


def detect_vehicle(scan_id: str, image_path_override: Optional[str] = None) -> dict:
    _log_pipeline_step(scan_id, 'capture-start', source='image-path' if image_path_override else 'camera')
    if image_path_override:
        source_path = Path(image_path_override)
        if not source_path.exists():
            raise RuntimeError('Provided imagePath does not exist')
        saved_path = build_capture_path()
        shutil.copyfile(source_path, saved_path)
    else:
        saved_path = capture_gate_image()
    relative_path = relative_upload_path(saved_path)
    _log_pipeline_step(scan_id, 'capture-complete', imagePath=relative_path)
    update_gate_scan(scan_id, status='CAPTURED', image_path=relative_path, image_filename=saved_path.name)
    ocr = run_ocr(saved_path)
    _log_pipeline_step(scan_id, 'ocr-complete', plate=ocr['plate'], mode=ocr['mode'])
    ocr['imagePath'] = relative_path
    update_gate_scan(scan_id, status='OCR_COMPLETE', normalized_plate=ocr['plate'], ocr_raw_text=ocr['rawText'], ocr_mode=ocr['mode'], vehicle_type=ocr['vehicle']['type'], vehicle_owner=ocr['vehicle']['owner'], vehicle_registered=int(bool(ocr['vehicle']['registered'])))
    return ocr


def process_exit(scan_id: str, booking_row, detection: dict, source: str) -> dict:
    _log_pipeline_step(scan_id, 'exit-match', bookingId=booking_row['_id'], plate=detection['plate'], source=source)
    updated_booking = update_smart_booking_status(booking_row['_id'], 'COMPLETED')
    freed_slot = updated_booking['slotId']
    update_lcd('Slot Freed', freed_slot['label'] if freed_slot else 'Cleared')
    emit_progress({'step': 'slot-freed', 'plate': detection['plate'], 'slot': freed_slot, 'source': source})
    open_gate()
    result = {'success': True, 'action': 'EXIT', 'message': 'Vehicle exiting, slot released', 'slot': freed_slot, 'booking': updated_booking, 'plate': detection['plate'], 'vehicle': detection['vehicle'], 'ocrMode': detection['mode'], 'imagePath': detection['imagePath'], 'source': source, 'scanId': scan_id}
    update_gate_scan(scan_id, status='COMPLETED', action='EXIT', booking_id=updated_booking['_id'], slot_id=freed_slot['_id'] if freed_slot else None, notes=result['message'], processed_at=now_iso())
    emit_gate_event(result)
    return result


def process_reserved_entry(scan_id: str, booking_row, detection: dict, source: str) -> dict:
    _log_pipeline_step(scan_id, 'booked-entry-match', bookingId=booking_row['_id'], plate=detection['plate'], source=source)
    updated_booking = update_smart_booking_status(booking_row['_id'], 'ACTIVE')
    reserved_slot = updated_booking['slotId']
    update_lcd('Gate Open', reserved_slot['label'] if reserved_slot else 'Booked')
    emit_progress({'step': 'booking-activated', 'plate': detection['plate'], 'slot': reserved_slot, 'source': source, 'bookingId': updated_booking['_id']})
    open_gate()
    result = {
        'success': True,
        'action': 'ENTRY',
        'message': 'Booked vehicle verified. Gate opened',
        'slot': reserved_slot,
        'booking': updated_booking,
        'plate': detection['plate'],
        'vehicle': detection['vehicle'],
        'ocrMode': detection['mode'],
        'imagePath': detection['imagePath'],
        'source': source,
        'scanId': scan_id,
    }
    update_gate_scan(
        scan_id,
        status='COMPLETED',
        action='ENTRY',
        booking_id=updated_booking['_id'],
        slot_id=reserved_slot['_id'] if reserved_slot else None,
        notes=result['message'],
        processed_at=now_iso(),
    )
    emit_gate_event(result)
    return result


def process_entry(scan_id: str, detection: dict, source: str, duration_hours: int) -> dict:
    _log_pipeline_step(scan_id, 'new-entry', plate=detection['plate'], durationHours=duration_hours, source=source)
    update_lcd('Checking Slots', detection['plate'])
    emit_progress({'step': 'checking-slots', 'plate': detection['plate'], 'source': source})
    automation_user = ensure_automation_user()
    try:
        booking = create_smart_booking(automation_user['id'], detection['vehicle']['type'], duration_hours, detection['plate'])
        booking = update_smart_booking_status(booking['_id'], 'ACTIVE')
    except Exception:
        update_lcd('Invalid Entry', 'No Slot Avail')
        send_command('GATE_CLOSE')
        result = {'success': False, 'action': 'ENTRY_DENIED', 'message': 'Invalid entry', 'reason': 'No slot available', 'plate': detection['plate'], 'vehicle': detection['vehicle'], 'ocrMode': detection['mode'], 'imagePath': detection['imagePath'], 'source': source, 'scanId': scan_id}
        update_gate_scan(scan_id, status='COMPLETED', action='ENTRY_DENIED', notes=result['message'], processed_at=now_iso())
        emit_progress({'step': 'entry-denied', 'plate': detection['plate'], 'source': source, 'message': result['message'], 'reason': result['reason']})
        emit_gate_event(result)
        return result
    updated_slot = booking['slotId']
    update_lcd('Gate Open', updated_slot['label'])
    emit_progress({'step': 'entry-approved', 'plate': detection['plate'], 'slot': updated_slot, 'source': source, 'bookingId': booking['_id']})
    open_gate()
    result = {'success': True, 'action': 'ENTRY', 'message': 'Slot assigned. Gate opened', 'slot': updated_slot, 'booking': booking, 'plate': detection['plate'], 'vehicle': detection['vehicle'], 'ocrMode': detection['mode'], 'imagePath': detection['imagePath'], 'source': source, 'scanId': scan_id}
    update_gate_scan(scan_id, status='COMPLETED', action='ENTRY', booking_id=booking['_id'], slot_id=updated_slot['_id'], notes=result['message'], processed_at=now_iso())
    emit_gate_event(result)
    return result


def process_gate_entry(source: str = 'gate-sensor', duration_hours: Optional[int] = None, image_path: Optional[str] = None) -> dict:
    global _last_triggered_at
    with _scan_lock:
        now_ms = time.time() * 1000
        if now_ms - _last_triggered_at < GATE_ENTRY_DEBOUNCE_MS:
            return {'success': False, 'ignored': True, 'message': 'Gate event ignored because a recent scan was already processed'}
        _last_triggered_at = now_ms
    scan_id = create_gate_scan(source)
    try:
        _log_pipeline_step(scan_id, 'trigger-received', source=source)
        update_lcd('Vehicle Detected', 'Please Wait')
        emit_progress({'step': 'vehicle-detected', 'source': source, 'scanId': scan_id})
        update_lcd('Starting Camera', 'Capturing')
        emit_progress({'step': 'camera-start', 'source': source, 'scanId': scan_id})
        detection = detect_vehicle(scan_id, image_path)
        if not detection['plate'] or detection['plate'] == 'UNKNOWN':
            result = {
                'success': False,
                'action': 'SCAN_FAILED',
                'message': 'Invalid entry',
                'reason': 'Number plate scan failed',
                'plate': detection['plate'],
                'vehicle': detection['vehicle'],
                'ocrMode': detection['mode'],
                'imagePath': detection['imagePath'],
                'source': source,
                'scanId': scan_id,
            }
            _log_pipeline_step(scan_id, 'scan-failed', level=logging.INFO, source=source, reason=result['reason'])
            update_lcd('Invalid Entry', 'No Plate Found')
            send_command('GATE_CLOSE')
            emit_progress({'step': 'scan-failed', 'source': source, 'message': result['message'], 'reason': result['reason'], 'scanId': scan_id})
            update_gate_scan(scan_id, status='COMPLETED', action='SCAN_FAILED', notes=result['message'], processed_at=now_iso())
            emit_gate_event(result)
            return result
        emit_progress({'step': 'ocr-complete', 'source': source, 'plate': detection['plate'], 'imagePath': detection['imagePath'], 'ocrMode': detection['mode'], 'vehicle': detection['vehicle'], 'scanId': scan_id})
        update_lcd('Plate Detected', detection['plate'] or 'Unknown')
        existing = fetch_gate_booking_by_plate(detection['plate'])
        if existing and existing.get('status') == 'ACTIVE':
            result = process_exit(scan_id, existing, detection, source)
        elif existing and existing.get('status') in {'PENDING', 'PAYMENT_PENDING'}:
            result = process_reserved_entry(scan_id, existing, detection, source)
        else:
            result = process_entry(scan_id, detection, source, int(duration_hours or AUTO_BOOKING_DURATION_HOURS))
        _log_pipeline_step(scan_id, 'completed', action=result.get('action'), plate=result.get('plate'), source=source)
        logger.info(
            'Scan result | action=%s | plate=%s | slot=%s | source=%s',
            result.get('action', 'UNKNOWN'),
            result.get('plate', 'UNKNOWN'),
            (result.get('slot') or {}).get('label', 'NONE'),
            source,
        )
        return result
    except Exception as exc:
        _log_pipeline_step(scan_id, 'failed', level=logging.ERROR, source=source, message=str(exc))
        logger.exception('Gate pipeline failed for scan %s', scan_id)
        logger.error('Scan error | source=%s | message=%s', source, str(exc))
        update_lcd('Error Occurred', 'Check Backend')
        emit_progress({'step': 'error', 'source': source, 'message': str(exc), 'scanId': scan_id})
        update_gate_scan(scan_id, status='FAILED', action='ERROR', error_message=str(exc), processed_at=now_iso())
        raise


def complete_payment_and_open_gate(booking_id: str, source: str = 'payment-page') -> dict:
    booking = fetch_booking_by_id(booking_id)
    if not booking:
        raise RuntimeError('Booking not found')
    if booking['status'] == 'ACTIVE':
        open_gate()
        return {'success': True, 'booking': booking, 'message': 'Booking already active. Gate opened.', 'action': 'ENTRY'}
    if booking['status'] not in {'PENDING', 'PAYMENT_PENDING'}:
        raise RuntimeError('This booking is not available for gate entry.')

    updated_booking = update_smart_booking_status(booking_id, 'ACTIVE')
    occupied_slot = updated_booking['slotId']
    update_lcd('Gate Open', occupied_slot['label'] if occupied_slot else 'Paid')
    emit_progress({'step': 'booking-activated', 'bookingId': booking_id, 'slot': occupied_slot, 'source': source})
    open_gate()
    emit_gate_event({
        'success': True,
        'action': 'ENTRY',
        'message': 'Gate opened and slot marked occupied',
        'slot': occupied_slot,
        'booking': updated_booking,
        'plate': updated_booking['numberPlate'],
        'source': source,
    })
    logger.info(
        'Scan result | action=%s | plate=%s | slot=%s | source=%s',
        'ENTRY',
        updated_booking.get('numberPlate', 'UNKNOWN'),
        (occupied_slot or {}).get('label', 'NONE'),
        source,
    )
    return {
        'success': True,
        'booking': updated_booking,
        'slot': occupied_slot,
        'message': 'Gate opened and slot marked occupied',
        'action': 'ENTRY',
    }
