import logging
import os
import threading
import warnings
from functools import wraps

from flask import Flask, g, jsonify, request, send_from_directory

from python_backend.automation import complete_payment_and_open_gate, process_gate_entry
from python_backend.database import create_user, ensure_schema, get_user_by_email, get_user_by_id, seed_data
from python_backend.events import get_latest_events
from python_backend.hardware import serial_listener_loop
from python_backend.ocr import run_ocr, save_manual_upload
from python_backend.security import create_token, decode_token, hash_password, verify_password
from python_backend.settings import FRONTEND_URL, SERIAL_LISTENER_ENABLED, SIMULATION_MODE, UPLOAD_DIR
from python_backend.smart_backend import (
    bootstrap_smart_backend,
    create_smart_booking,
    fetch_booking_by_id,
    fetch_admin_overview,
    fetch_context,
    fetch_smart_slots,
    fetch_user_bookings,
    recommend_slot,
    start_expiry_worker,
    update_iot_slot,
    update_smart_booking_status,
    validate_qr_token,
)

app = Flask(__name__)
_serial_thread_started = False
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('python_backend.hardware').setLevel(logging.INFO)
logging.getLogger('python_backend.ocr').setLevel(logging.WARNING)
logging.getLogger('easyocr').setLevel(logging.ERROR)
warnings.filterwarnings('ignore', message='.*pin_memory.*')


def auth_required(admin: bool = False):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get('Authorization', '')
            if not header.startswith('Bearer '):
                return jsonify({'success': False, 'message': 'Authentication required'}), 401
            try:
                payload = decode_token(header.split(' ', 1)[1])
            except Exception:
                return jsonify({'success': False, 'message': 'Invalid or expired token'}), 401
            user = get_user_by_id(payload.get('id', ''))
            if not user:
                return jsonify({'success': False, 'message': 'User not found'}), 401
            if admin and user['role'] != 'admin':
                return jsonify({'success': False, 'message': 'Admin access required'}), 403
            g.current_user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', FRONTEND_URL)
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response


@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path: str):
    return ('', 204)


@app.route('/')
def root():
    return jsonify({'status': 'ok', 'backend': 'python', 'message': 'Advanced Smart Parking Python backend is running'})


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'backend': 'python'})


@app.route('/uploads/<path:filename>')
def uploads(filename: str):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route('/events/latest')
@auth_required()
def events_latest():
    return jsonify(get_latest_events())


@app.route('/auth/register', methods=['POST'])
def auth_register():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    phone = (data.get('phone') or '').strip() or None
    if not name or not email or not password:
        return jsonify({'success': False, 'message': 'Name, email, and password are required'}), 400
    if get_user_by_email(email):
        return jsonify({'success': False, 'message': 'Email already registered'}), 409
    user = create_user(name, email, hash_password(password), phone)
    token = create_token({'id': user['id'], 'role': user['role']})
    return jsonify({'success': True, 'token': token, 'user': user}), 201


@app.route('/auth/login', methods=['POST'])
def auth_login():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    user = get_user_by_email(email)
    if not user or not verify_password(password, user['password_hash']):
        return jsonify({'success': False, 'message': 'Invalid email or password'}), 401
    token = create_token({'id': user['id'], 'role': user['role']})
    return jsonify({'success': True, 'token': token, 'user': get_user_by_id(user['id'])})


@app.route('/auth/me')
@auth_required()
def auth_me():
    return jsonify({'success': True, 'user': g.current_user})


@app.route('/slots')
@auth_required()
def slots_route():
    return jsonify(fetch_smart_slots())


@app.route('/parking/context')
@auth_required()
def parking_context_route():
    return jsonify(fetch_context())


@app.route('/parking/recommendation', methods=['POST'])
@auth_required()
def parking_recommendation_route():
    data = request.get_json(force=True, silent=True) or {}
    try:
        payload = recommend_slot(
            vehicle_type=data.get('vehicleType', 'car'),
            preference=data.get('preference') or {},
            location=data.get('location') or {},
        )
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    return jsonify({'success': True, **payload})


@app.route('/bookings', methods=['POST'])
@auth_required()
def create_booking_route():
    data = request.get_json(force=True, silent=True) or {}
    try:
        booking = create_smart_booking(
            g.current_user['id'],
            vehicle_type=data.get('vehicleType', 'car'),
            duration_hours=int(data.get('durationHours') or 1),
            number_plate=data.get('numberPlate') or '',
            preference=data.get('preference') or {},
            location=data.get('location') or {},
        )
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    return jsonify({'success': True, 'booking': booking}), 201


@app.route('/bookings/my')
@auth_required()
def my_bookings_route():
    return jsonify({'bookings': fetch_user_bookings(g.current_user['id'])})


@app.route('/bookings/<booking_id>')
@auth_required()
def booking_detail_route(booking_id: str):
    booking = fetch_booking_by_id(booking_id)
    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found'}), 404
    if booking['userId'] and booking['userId']['_id'] != g.current_user['id'] and g.current_user['role'] != 'admin':
        return jsonify({'success': False, 'message': 'Not allowed'}), 403
    return jsonify({'success': True, 'booking': booking})


@app.route('/bookings/<booking_id>/checkin', methods=['POST'])
@auth_required()
def checkin_route(booking_id: str):
    booking = fetch_booking_by_id(booking_id)
    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found'}), 404
    if booking['userId'] and booking['userId']['_id'] != g.current_user['id'] and g.current_user['role'] != 'admin':
        return jsonify({'success': False, 'message': 'Not allowed'}), 403
    try:
        result = complete_payment_and_open_gate(booking_id, source='checkin-route')
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    return jsonify(result)


@app.route('/bookings/<booking_id>/payment/complete', methods=['POST'])
@auth_required()
def complete_payment_route(booking_id: str):
    booking = fetch_booking_by_id(booking_id)
    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found'}), 404
    if booking['userId'] and booking['userId']['_id'] != g.current_user['id'] and g.current_user['role'] != 'admin':
        return jsonify({'success': False, 'message': 'Not allowed'}), 403
    try:
        result = complete_payment_and_open_gate(booking_id, source='payment-page')
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409
    return jsonify(result)


@app.route('/bookings/<booking_id>/checkout', methods=['POST'])
@auth_required()
def checkout_route(booking_id: str):
    booking = fetch_booking_by_id(booking_id)
    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found'}), 404
    if booking['userId'] and booking['userId']['_id'] != g.current_user['id'] and g.current_user['role'] != 'admin':
        return jsonify({'success': False, 'message': 'Not allowed'}), 403
    try:
        booking = update_smart_booking_status(booking_id, 'COMPLETED')
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404
    return jsonify({'success': True, 'booking': booking})


@app.route('/gate/scan-qr', methods=['POST'])
def gate_scan_qr_route():
    data = request.get_json(force=True, silent=True) or {}
    try:
        result = validate_qr_token(data.get('qrToken') or '')
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    return jsonify(result)


@app.route('/iot/slot-state', methods=['POST'])
@auth_required(admin=True)
def iot_slot_state_route():
    data = request.get_json(force=True, silent=True) or {}
    try:
        slot = update_iot_slot(data.get('slotCode') or '', data.get('status') or 'FREE')
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404
    return jsonify({'success': True, 'slot': slot})


@app.route('/admin/overview')
@auth_required(admin=True)
def admin_overview_route():
    return jsonify(fetch_admin_overview())


@app.route('/capture-plate', methods=['POST'])
@auth_required()
def capture_plate_route():
    if 'plate_image' not in request.files:
        return jsonify({'success': False, 'message': 'No image uploaded'}), 400
    saved = save_manual_upload(request.files['plate_image'])
    try:
        ocr = run_ocr(saved)
        if not ocr['plate'] or ocr['plate'] == 'UNKNOWN':
            return jsonify({'success': False, 'message': 'Number plate scan failed'}), 422
        return jsonify({'success': True, 'plate': ocr['plate'], 'vehicle': ocr['vehicle'], 'mode': ocr['mode'], 'rawText': ocr['rawText']})
    finally:
        saved.unlink(missing_ok=True)


@app.route('/gate/entry-event', methods=['POST'])
def gate_entry_route():
    data = request.get_json(force=True, silent=True) or {}
    try:
        result = process_gate_entry(source=data.get('source') or 'gate-sensor', duration_hours=data.get('durationHours'), image_path=data.get('imagePath'))
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500
    if result.get('ignored'):
        return jsonify(result), 202
    if not result.get('success'):
        return jsonify(result), 409
    return jsonify(result), 201


def bootstrap():
    global _serial_thread_started
    ensure_schema()
    seed_data()
    bootstrap_smart_backend()
    start_expiry_worker()
    if not _serial_thread_started and not SIMULATION_MODE and SERIAL_LISTENER_ENABLED:
        _serial_thread_started = True
        logger.info('Starting serial listener thread')
        threading.Thread(target=serial_listener_loop, args=(lambda source: process_gate_entry(source=source),), daemon=True).start()
    else:
        logger.info('Serial listener not started. simulation=%s enabled=%s started=%s', SIMULATION_MODE, SERIAL_LISTENER_ENABLED, _serial_thread_started)


bootstrap()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=False, threaded=True)
