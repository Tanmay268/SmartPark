import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Dict, Optional

from .events import emit_progress, now_iso
from .security import hash_password
from .settings import DB_PATH, SYSTEM_AUTOMATION_EMAIL

_db_lock = RLock()

MOCK_VEHICLES = {
    'MH12AB1234': {'type': 'car', 'owner': 'Rahul Sharma', 'registered': True},
    'KA05CD5678': {'type': 'bike', 'owner': 'Priya Singh', 'registered': True},
    'DL01EF9012': {'type': 'truck', 'owner': 'Amit Kumar', 'registered': True},
    'TN09GH3456': {'type': 'car', 'owner': 'Kavya R', 'registered': True},
}


def normalize_plate(value: str = '') -> str:
    return re.sub(r'[^A-Z0-9]', '', (value or '').upper()).strip()


def make_id(prefix: str) -> str:
    return f'{prefix}_{secrets.token_hex(8)}'


@contextmanager
def db_cursor(commit: bool = False):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            yield conn, cur
            if commit:
                conn.commit()
        finally:
            conn.close()


def dict_from_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def ensure_schema() -> None:
    with db_cursor(commit=True) as (_, cur):
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pricing (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                car REAL NOT NULL,
                bike REAL NOT NULL,
                truck REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS slots (
                id TEXT PRIMARY KEY,
                slot_number INTEGER NOT NULL UNIQUE,
                label TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                vehicle_type TEXT,
                current_booking_id TEXT,
                sensor_pin INTEGER,
                last_updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                vehicle_type TEXT NOT NULL,
                number_plate TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                duration_hours INTEGER NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL,
                checked_in_at TEXT,
                checked_out_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gate_scans (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                action TEXT NOT NULL,
                image_path TEXT,
                image_filename TEXT,
                ocr_raw_text TEXT,
                normalized_plate TEXT,
                ocr_mode TEXT,
                vehicle_type TEXT,
                vehicle_owner TEXT,
                vehicle_registered INTEGER,
                booking_id TEXT,
                slot_id TEXT,
                notes TEXT,
                error_message TEXT,
                processed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def seed_data() -> None:
    with db_cursor(commit=True) as (_, cur):
        cur.execute('SELECT COUNT(*) AS count FROM users')
        if cur.fetchone()['count'] == 0:
            cur.execute(
                'INSERT INTO users (id, name, email, phone, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (make_id('usr'), 'Admin User', 'admin@parking.com', '+91 9999999999', hash_password('admin123'), 'admin', now_iso()),
            )
        cur.execute('SELECT COUNT(*) AS count FROM pricing')
        if cur.fetchone()['count'] == 0:
            cur.execute('INSERT INTO pricing (id, car, bike, truck, updated_at) VALUES (1, 50, 20, 80, ?)', (now_iso(),))
        cur.execute('SELECT COUNT(*) AS count FROM slots')
        if cur.fetchone()['count'] == 0:
            cur.executemany(
                'INSERT INTO slots (id, slot_number, label, status, vehicle_type, current_booking_id, sensor_pin, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    (make_id('slot'), 1, 'A1', 'FREE', None, None, 2, now_iso()),
                    (make_id('slot'), 2, 'A2', 'FREE', None, None, 3, now_iso()),
                    (make_id('slot'), 3, 'A3', 'FREE', None, None, 4, now_iso()),
                    (make_id('slot'), 4, 'A4', 'FREE', None, None, 5, now_iso()),
                ],
            )


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    with db_cursor() as (_, cur):
        cur.execute('SELECT id, name, email, phone, role, created_at FROM users WHERE id = ?', (user_id,))
        return dict_from_row(cur.fetchone())


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with db_cursor() as (_, cur):
        cur.execute('SELECT * FROM users WHERE email = ?', (email.lower(),))
        return dict_from_row(cur.fetchone())


def create_user(name: str, email: str, password_hash_value: str, phone: str | None, role: str = 'user') -> Dict[str, Any]:
    user_id = make_id('usr')
    with db_cursor(commit=True) as (_, cur):
        cur.execute(
            'INSERT INTO users (id, name, email, phone, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (user_id, name, email.lower(), phone, password_hash_value, role, now_iso()),
        )
    return get_user_by_id(user_id)


def ensure_automation_user() -> Dict[str, Any]:
    existing = get_user_by_email(SYSTEM_AUTOMATION_EMAIL)
    if existing:
        return get_user_by_id(existing['id'])
    return create_user('Gate Automation', SYSTEM_AUTOMATION_EMAIL, hash_password(secrets.token_hex(12)), None, 'admin')


def fetch_pricing() -> Dict[str, float]:
    with db_cursor() as (_, cur):
        cur.execute('SELECT car, bike, truck FROM pricing WHERE id = 1')
        row = cur.fetchone()
        return {'car': row['car'], 'bike': row['bike'], 'truck': row['truck']}


def update_pricing(car: float, bike: float, truck: float) -> Dict[str, float]:
    with db_cursor(commit=True) as (_, cur):
        cur.execute('UPDATE pricing SET car = ?, bike = ?, truck = ?, updated_at = ? WHERE id = 1', (car, bike, truck, now_iso()))
    return fetch_pricing()


def serialize_slot(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        '_id': row['id'],
        'slotNumber': row['slot_number'],
        'label': row['label'],
        'status': row['status'],
        'vehicleType': row['vehicle_type'],
        'currentBookingId': row['current_booking_id'],
        'sensorPin': row['sensor_pin'],
        'lastUpdated': row['last_updated'],
    }


def fetch_slots_payload() -> Dict[str, Any]:
    with db_cursor() as (_, cur):
        cur.execute('SELECT * FROM slots ORDER BY slot_number ASC')
        rows = cur.fetchall()
    slots = [serialize_slot(row) for row in rows]
    summary = {
        'total': len(slots),
        'free': sum(1 for slot in slots if slot['status'] == 'FREE'),
        'booked': sum(1 for slot in slots if slot['status'] == 'BOOKED'),
        'occupied': sum(1 for slot in slots if slot['status'] == 'OCCUPIED'),
    }
    return {'slots': slots, 'summary': summary}


def fetch_slot(slot_id: str):
    with db_cursor() as (_, cur):
        cur.execute('SELECT * FROM slots WHERE id = ?', (slot_id,))
        return cur.fetchone()


def update_slot(slot_id: str, status: str, vehicle_type: Optional[str], booking_id: Optional[str]) -> Dict[str, Any]:
    with db_cursor(commit=True) as (_, cur):
        cur.execute('UPDATE slots SET status = ?, vehicle_type = ?, current_booking_id = ?, last_updated = ? WHERE id = ?', (status, vehicle_type, booking_id, now_iso(), slot_id))
        cur.execute('SELECT * FROM slots WHERE id = ?', (slot_id,))
        row = cur.fetchone()
    payload = serialize_slot(row)
    emit_progress({'step': 'slot-update', 'slot': payload, 'slotId': slot_id})
    return payload


def serialize_booking(row: sqlite3.Row, include_user: bool = True) -> Dict[str, Any]:
    with db_cursor() as (_, cur):
        cur.execute('SELECT id, label FROM slots WHERE id = ?', (row['slot_id'],))
        slot = cur.fetchone()
        user = None
        if include_user:
            cur.execute('SELECT id, name, email, phone, role FROM users WHERE id = ?', (row['user_id'],))
            user = cur.fetchone()
    return {
        '_id': row['id'],
        'userId': {'_id': user['id'], 'name': user['name'], 'email': user['email'], 'phone': user['phone'], 'role': user['role']} if user else row['user_id'],
        'slotId': {'_id': slot['id'], 'label': slot['label']} if slot else row['slot_id'],
        'vehicleType': row['vehicle_type'],
        'numberPlate': row['number_plate'],
        'startTime': row['start_time'],
        'endTime': row['end_time'],
        'durationHours': row['duration_hours'],
        'price': row['price'],
        'status': row['status'],
        'checkedInAt': row['checked_in_at'],
        'checkedOutAt': row['checked_out_at'],
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


def fetch_booking(booking_id: str):
    with db_cursor() as (_, cur):
        cur.execute('SELECT * FROM bookings WHERE id = ?', (booking_id,))
        return cur.fetchone()


def delete_booking(booking_id: str) -> Optional[Dict[str, Any]]:
    booking = fetch_booking(booking_id)
    if not booking:
        return None
    payload = serialize_booking(booking)
    with db_cursor(commit=True) as (_, cur):
        cur.execute('DELETE FROM bookings WHERE id = ?', (booking_id,))
    return payload


def fetch_bookings_for_user(user_id: str) -> list[Dict[str, Any]]:
    with db_cursor() as (_, cur):
        cur.execute('SELECT * FROM bookings WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        rows = cur.fetchall()
    return [serialize_booking(row, include_user=False) for row in rows]


def fetch_all_bookings() -> list[Dict[str, Any]]:
    with db_cursor() as (_, cur):
        cur.execute('SELECT * FROM bookings ORDER BY created_at DESC')
        rows = cur.fetchall()
    return [serialize_booking(row) for row in rows]


def fetch_latest_booking_by_plate(plate: str):
    with db_cursor() as (_, cur):
        cur.execute("SELECT * FROM bookings WHERE number_plate = ? AND status IN ('PENDING', 'ACTIVE') ORDER BY created_at DESC LIMIT 1", (plate,))
        return cur.fetchone()


def create_booking(user_id: str, slot_id: str, vehicle_type: str, plate: str, duration_hours: int, status: str) -> Dict[str, Any]:
    booking_id = make_id('bk')
    pricing = fetch_pricing()
    start_dt = datetime.now(timezone.utc)
    start = start_dt.isoformat()
    end = (start_dt + timedelta(hours=duration_hours)).isoformat()
    with db_cursor(commit=True) as (_, cur):
        cur.execute(
            'INSERT INTO bookings (id, user_id, slot_id, vehicle_type, number_plate, start_time, end_time, duration_hours, price, status, checked_in_at, checked_out_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (booking_id, user_id, slot_id, vehicle_type, plate, start, end, duration_hours, float(pricing[vehicle_type]) * duration_hours, status, start if status == 'ACTIVE' else None, None, start, start),
        )
        cur.execute('SELECT * FROM bookings WHERE id = ?', (booking_id,))
        row = cur.fetchone()
    return serialize_booking(row)


def update_booking_status(booking_id: str, status: str, checked_field: Optional[str] = None) -> Dict[str, Any]:
    checked_in = now_iso() if checked_field == 'checked_in_at' else None
    checked_out = now_iso() if checked_field == 'checked_out_at' else None
    with db_cursor(commit=True) as (_, cur):
        cur.execute('UPDATE bookings SET status = ?, checked_in_at = COALESCE(?, checked_in_at), checked_out_at = COALESCE(?, checked_out_at), updated_at = ? WHERE id = ?', (status, checked_in, checked_out, now_iso(), booking_id))
        cur.execute('SELECT * FROM bookings WHERE id = ?', (booking_id,))
        row = cur.fetchone()
    return serialize_booking(row)


def create_gate_scan(source: str, trigger_type: str = 'IR_SENSOR', event_type: str = 'ENTRY_SCAN') -> str:
    scan_id = make_id('scan')
    ts = now_iso()
    with db_cursor(commit=True) as (_, cur):
        cur.execute('INSERT INTO gate_scans (id, source, trigger_type, event_type, status, action, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (scan_id, source, trigger_type, event_type, 'DETECTED', 'UNKNOWN', ts, ts))
    return scan_id


def update_gate_scan(scan_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields['updated_at'] = now_iso()
    columns = ', '.join(f'{key} = ?' for key in fields.keys())
    values = list(fields.values()) + [scan_id]
    with db_cursor(commit=True) as (_, cur):
        cur.execute(f'UPDATE gate_scans SET {columns} WHERE id = ?', values)


def vehicle_info_for_plate(plate: str) -> Dict[str, Any]:
    normalized = normalize_plate(plate)
    vehicle = MOCK_VEHICLES.get(normalized, {'type': 'car', 'owner': 'Unknown', 'registered': False})
    return {'plate': normalized, 'vehicle': vehicle}


def fetch_stats() -> Dict[str, Any]:
    with db_cursor() as (_, cur):
        cur.execute('SELECT COUNT(*) AS count FROM users')
        total_users = cur.fetchone()['count']
        cur.execute('SELECT COUNT(*) AS count FROM bookings')
        total_bookings = cur.fetchone()['count']
        cur.execute("SELECT COALESCE(SUM(price), 0) AS revenue FROM bookings WHERE status IN ('ACTIVE', 'COMPLETED')")
        revenue = cur.fetchone()['revenue']
    return {'totalUsers': total_users, 'totalBookings': total_bookings, 'revenue': revenue, 'slots': fetch_slots_payload()['summary']}
