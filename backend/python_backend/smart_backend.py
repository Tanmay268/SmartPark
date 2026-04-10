import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests

from .database import create_user, fetch_pricing as fetch_legacy_pricing, get_user_by_email, make_id
from .events import emit_app_event, now_iso
from .security import create_token, decode_token, hash_password
from .settings import DB_PATH, SYSTEM_AUTOMATION_EMAIL

SMART_DB_LOCK = threading.RLock()
EXPIRY_THREAD_STARTED = False
EMERGENCY_TYPES = {'ambulance', 'fire_truck', 'police'}
SUPPORTED_VEHICLES = {'car', 'bike', 'truck', 'ambulance', 'fire_truck', 'police'}
SEEDED_SAMPLE_EMAILS = {
    'rahul@parking.com',
    'priya@parking.com',
    'amit@parking.com',
    'kavya@parking.com',
}
LEGACY_SAMPLE_PLATES = ['HR98AA7777', 'DL01EF9012', 'KA05CD5678', 'MH12AB1234', 'PB10ZZ4521', 'UP32MN9087']
SAFE_SAMPLE_PLATES = ['SMP001CAR', 'SMP002BIKE', 'SMP003TRK', 'SMP004AMB', 'SMP005POL', 'SMP006FIR']


def env_int(name: str, default: int) -> int:
    import os
    return int(os.getenv(name, str(default)))


def env_float(name: str, default: float) -> float:
    import os
    return float(os.getenv(name, str(default)))


def env_str(name: str, default: str) -> str:
    import os
    return os.getenv(name, default)


def build_slot_layout(total_slots: int, floors: int, zone: str) -> list[dict[str, int | str]]:
    if total_slots <= 0:
        return []

    floors = max(1, floors)
    slots_per_floor = total_slots // floors
    extra_slots = total_slots % floors
    layout: list[dict[str, int | str]] = []

    for floor in range(1, floors + 1):
        floor_slots = slots_per_floor + (1 if floor <= extra_slots else 0)
        for position in range(floor_slots):
            row_index = (position // 5) + 1
            column_index = (position % 5) + 1
            code = f'{zone}{floor}-{row_index:02d}{column_index:02d}'
            layout.append(
                {
                    'zone': zone,
                    'floor': floor,
                    'row_index': row_index,
                    'column_index': column_index,
                    'code': code,
                }
            )

    return layout


@contextmanager
def smart_cursor(commit: bool = False):
    with SMART_DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            yield conn, cur
            if commit:
                conn.commit()
        finally:
            conn.close()


def normalize_plate(value: str = '') -> str:
    return ''.join(ch for ch in (value or '').upper() if ch.isalnum()).strip()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_json(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'))


def parse_json(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def ensure_smart_schema() -> None:
    with smart_cursor(commit=True) as (_, cur):
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS smart_slots (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL UNIQUE,
                zone_name TEXT NOT NULL,
                floor INTEGER NOT NULL,
                row_index INTEGER NOT NULL,
                column_index INTEGER NOT NULL,
                covered INTEGER NOT NULL DEFAULT 0,
                shaded INTEGER NOT NULL DEFAULT 0,
                near_lift INTEGER NOT NULL DEFAULT 0,
                reserved_for TEXT NOT NULL DEFAULT 'none',
                status TEXT NOT NULL DEFAULT 'FREE',
                distance_to_entry REAL NOT NULL DEFAULT 0,
                distance_to_exit REAL NOT NULL DEFAULT 0,
                sensor_id TEXT,
                current_booking_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                last_updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS smart_bookings (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                vehicle_type TEXT NOT NULL,
                emergency_type TEXT NOT NULL DEFAULT 'none',
                number_plate TEXT,
                location_json TEXT NOT NULL DEFAULT '{}',
                preference_json TEXT NOT NULL DEFAULT '{}',
                weather_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                booking_expires_at TEXT,
                checked_in_at TEXT,
                checked_out_at TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                duration_hours INTEGER NOT NULL,
                price REAL NOT NULL,
                qr_token TEXT NOT NULL,
                qr_svg TEXT NOT NULL,
                recommendation_json TEXT NOT NULL DEFAULT '{}',
                navigation_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS smart_parking_logs (
                id TEXT PRIMARY KEY,
                booking_id TEXT,
                slot_id TEXT,
                event_type TEXT NOT NULL,
                occupancy_level REAL NOT NULL,
                total_slots INTEGER NOT NULL,
                occupied_slots INTEGER NOT NULL,
                zone_name TEXT,
                floor INTEGER,
                weather_category TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS smart_weather_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS smart_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


def ensure_smart_users() -> None:
    if not get_user_by_email('admin@parking.com'):
        create_user('Admin User', 'admin@parking.com', hash_password('admin123'), '+91 9999999999', 'admin')
    if not get_user_by_email(SYSTEM_AUTOMATION_EMAIL):
        create_user('Gate Automation', SYSTEM_AUTOMATION_EMAIL, hash_password(secrets.token_hex(12)), None, 'admin')


def seed_smart_slots() -> None:
    with smart_cursor(commit=True) as (_, cur):
        cur.execute('SELECT COUNT(*) AS count FROM smart_slots')
        if cur.fetchone()['count'] > 0:
            return

        floors = env_int('PARKING_FLOORS', 4)
        reserved_emergency = env_int('RESERVED_EMERGENCY_SLOTS_PER_FLOOR', 2)
        max_slots = env_int('SMART_TOTAL_SLOTS', 100)
        zone = env_str('SMART_PRIMARY_ZONE', 'A').strip() or 'A'
        now = now_iso()
        payload = []
        layout = build_slot_layout(max_slots, floors, zone)
        for slot in layout:
            floor = int(slot['floor'])
            row_index = int(slot['row_index'])
            column_index = int(slot['column_index'])
            label = str(slot['code'])
            payload.append(
                (
                    make_id('sslot'),
                    label,
                    label,
                    str(slot['zone']),
                    floor,
                    row_index,
                    column_index,
                    1 if floor <= 2 or column_index % 3 == 0 else 0,
                    1 if column_index % 2 == 0 else 0,
                    1 if column_index >= 4 else 0,
                    'emergency' if row_index == 1 and column_index <= reserved_emergency else 'none',
                    'FREE',
                    float(floor * 8 + row_index * 2 + column_index),
                    float(floor * 5 + row_index * 2 + max(1, column_index - 1)),
                    f'S-{zone}-{floor}-{row_index}-{column_index}',
                    None,
                    serialize_json({'zone': zone, 'floor': floor, 'row': row_index, 'column': column_index}),
                    now,
                )
            )
        cur.executemany(
            """
            INSERT INTO smart_slots (
                id, code, label, zone_name, floor, row_index, column_index, covered, shaded, near_lift,
                reserved_for, status, distance_to_entry, distance_to_exit, sensor_id, current_booking_id,
                metadata_json, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )


def rebalance_smart_slots(total_slots: Optional[int] = None, floors: Optional[int] = None) -> None:
    target_total = total_slots or env_int('SMART_TOTAL_SLOTS', 100)
    target_floors = floors or env_int('PARKING_FLOORS', 4)
    target_zone = env_str('SMART_PRIMARY_ZONE', 'A').strip() or 'A'

    with smart_cursor(commit=True) as (_, cur):
        cur.execute('SELECT * FROM smart_slots ORDER BY zone_name, floor, row_index, column_index')
        rows = cur.fetchall()
        if not rows:
            return

        layout = build_slot_layout(target_total, target_floors, target_zone)
        if len(rows) != len(layout):
            return

        current_floors = sorted({row['floor'] for row in rows})
        counts_by_floor: Dict[int, int] = {}
        for row in rows:
            counts_by_floor[row['floor']] = counts_by_floor.get(row['floor'], 0) + 1

        expected_per_floor = [sum(1 for item in layout if int(item['floor']) == floor) for floor in range(1, target_floors + 1)]
        if current_floors == list(range(1, target_floors + 1)) and [counts_by_floor.get(floor, 0) for floor in range(1, target_floors + 1)] == expected_per_floor:
            return

        now = now_iso()
        temp_updates = []
        updates = []
        for row, slot in zip(rows, layout):
            floor = int(slot['floor'])
            row_index = int(slot['row_index'])
            column_index = int(slot['column_index'])
            code = str(slot['code'])
            temp_code = f"TMP-{row['id']}"
            temp_updates.append((temp_code, temp_code, row['id']))
            updates.append(
                (
                    code,
                    code,
                    str(slot['zone']),
                    floor,
                    row_index,
                    column_index,
                    1 if floor <= 2 or column_index % 3 == 0 else 0,
                    1 if column_index % 2 == 0 else 0,
                    1 if column_index >= 4 else 0,
                    'emergency' if row_index == 1 and column_index <= env_int('RESERVED_EMERGENCY_SLOTS_PER_FLOOR', 2) else 'none',
                    float(floor * 8 + row_index * 2 + column_index),
                    float(floor * 5 + row_index * 2 + max(1, column_index - 1)),
                    serialize_json({'zone': target_zone, 'floor': floor, 'row': row_index, 'column': column_index}),
                    now,
                    row['id'],
                )
            )

        cur.executemany(
            'UPDATE smart_slots SET code = ?, label = ? WHERE id = ?',
            temp_updates,
        )
        cur.executemany(
            """
            UPDATE smart_slots
            SET code = ?, label = ?, zone_name = ?, floor = ?, row_index = ?, column_index = ?,
                covered = ?, shaded = ?, near_lift = ?, reserved_for = ?,
                distance_to_entry = ?, distance_to_exit = ?, metadata_json = ?, last_updated = ?
            WHERE id = ?
            """,
            updates,
        )


def enforce_smart_slot_limit(limit: int = 100) -> None:
    with smart_cursor(commit=True) as (_, cur):
        cur.execute('SELECT COUNT(*) AS count FROM smart_slots')
        total = cur.fetchone()['count']
        if total <= limit:
            return

        cur.execute(
            """
            SELECT id
            FROM smart_slots
            WHERE status = 'FREE' AND current_booking_id IS NULL
            ORDER BY zone_name, floor, row_index, column_index
            """
        )
        removable_ids = [row['id'] for row in cur.fetchall()]
        remove_count = min(total - limit, len(removable_ids))
        if remove_count <= 0:
            return

        ids_to_delete = removable_ids[-remove_count:]
        cur.executemany('DELETE FROM smart_slots WHERE id = ?', ((slot_id,) for slot_id in ids_to_delete))


def seed_smart_history() -> None:
    with smart_cursor(commit=True) as (_, cur):
        cur.execute("SELECT value FROM smart_meta WHERE key = 'sample_history_seeded'")
        seeded = cur.fetchone()
        if seeded and seeded['value'] == '1':
            return

        sample_users = [
            ('Rahul Sharma', 'rahul@parking.com', '+91 9876500001'),
            ('Priya Singh', 'priya@parking.com', '+91 9876500002'),
            ('Amit Kumar', 'amit@parking.com', '+91 9876500003'),
            ('Kavya R', 'kavya@parking.com', '+91 9876500004'),
        ]
        user_ids = []
        for name, email, phone in sample_users:
            user = get_user_by_email(email)
            if not user:
                user = create_user(name, email, hash_password('user123'), phone, 'user')
            user_ids.append(user['id'])

        cur.execute('SELECT * FROM smart_slots ORDER BY zone_name, floor, row_index, column_index')
        slots = cur.fetchall()
        now = utc_now()
        vehicles = ['car', 'bike', 'truck', 'ambulance', 'police', 'fire_truck']
        sample_plates = SAFE_SAMPLE_PLATES

        for index, slot in enumerate(slots[:32]):
            vehicle_type = vehicles[index % len(vehicles)]
            user_id = user_ids[index % len(user_ids)]
            plate = sample_plates[index % len(sample_plates)]
            created_at = now - timedelta(hours=index * 3)
            duration_hours = 2 + (index % 4)
            start_time = created_at
            end_time = start_time + timedelta(hours=duration_hours)
            booking_id = make_id('sbk')
            status = 'COMPLETED'
            checked_in_at = start_time + timedelta(minutes=5)
            checked_out_at = end_time
            booking_expires_at = (start_time + timedelta(minutes=10)).isoformat()
            if index < 8:
                status = 'ACTIVE'
                checked_out_at = None
            elif index < 14:
                status = 'PENDING'
                checked_in_at = None
                checked_out_at = None
                booking_expires_at = (now + timedelta(minutes=20 + index)).isoformat()
            elif index < 18:
                status = 'EXPIRED'
                checked_in_at = None
                checked_out_at = None
                booking_expires_at = (start_time + timedelta(minutes=10)).isoformat()

            weather_category = 'rainy' if index % 5 == 0 else 'hot' if index % 4 == 0 else 'normal'
            weather_json = serialize_json({
                'category': weather_category,
                'temperatureC': 24 if weather_category == 'rainy' else 36 if weather_category == 'hot' else 29,
                'description': 'rain' if weather_category == 'rainy' else 'clear',
                'fetchedAt': created_at.isoformat(),
            })
            recommendation_json = serialize_json({
                'reason': 'Seeded booking history for analytics',
                'floorScore': 68 + (index % 7),
                'slotScore': 82 + (index % 11),
            })
            navigation_json = serialize_json(_navigation_for_slot(slot_payload(slot)))
            qr_token = create_token({'booking_id': booking_id, 'plate': plate, 'type': vehicle_type})
            qr_svg = _render_qr_svg(qr_token)
            price = _pricing_for_vehicle(vehicle_type) * duration_hours

            cur.execute(
                """
                INSERT INTO smart_bookings (
                    id, user_id, slot_id, vehicle_type, emergency_type, number_plate, location_json,
                    preference_json, weather_json, status, booking_expires_at, checked_in_at, checked_out_at,
                    start_time, end_time, duration_hours, price, qr_token, qr_svg, recommendation_json,
                    navigation_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    booking_id,
                    user_id,
                    slot['id'],
                    vehicle_type,
                    vehicle_type if vehicle_type in EMERGENCY_TYPES else 'none',
                    plate,
                    serialize_json({'city': 'Pune', 'lat': 18.5204, 'lon': 73.8567}),
                    serialize_json({'covered': bool(slot['covered']), 'shaded': bool(slot['shaded']), 'nearLift': bool(slot['near_lift'])}),
                    weather_json,
                    status,
                    booking_expires_at,
                    checked_in_at.isoformat() if checked_in_at else None,
                    checked_out_at.isoformat() if checked_out_at else None,
                    start_time.isoformat(),
                    end_time.isoformat(),
                    duration_hours,
                    price,
                    qr_token,
                    qr_svg,
                    recommendation_json,
                    navigation_json,
                    created_at.isoformat(),
                    created_at.isoformat(),
                ),
            )

            slot_status = 'FREE'
            current_booking_id = None
            if status == 'ACTIVE':
                slot_status = 'OCCUPIED'
                current_booking_id = booking_id
            elif status == 'PENDING':
                slot_status = 'BOOKED'
                current_booking_id = booking_id

            cur.execute(
                'UPDATE smart_slots SET status = ?, current_booking_id = ?, last_updated = ? WHERE id = ?',
                (slot_status, current_booking_id, created_at.isoformat(), slot['id']),
            )

        for step in range(72):
            created_at = now - timedelta(hours=71 - step)
            occupancy_level = 34 + ((step * 7) % 48)
            if 17 <= created_at.hour <= 21:
                occupancy_level = min(96, occupancy_level + 18)
            occupied_slots = round((occupancy_level / 100) * len(slots))
            cur.execute(
                """
                INSERT INTO smart_parking_logs (
                    id, booking_id, slot_id, event_type, occupancy_level, total_slots, occupied_slots,
                    zone_name, floor, weather_category, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_id('log'),
                    None,
                    None,
                    'BOOKED' if step % 3 else 'CHECKED_IN',
                    occupancy_level,
                    len(slots),
                    occupied_slots,
                    None,
                    None,
                    'rainy' if step % 6 == 0 else 'normal',
                    created_at.isoformat(),
                ),
            )
        cur.execute("INSERT OR REPLACE INTO smart_meta (key, value) VALUES ('sample_history_seeded', '1')")


def migrate_seed_sample_plates() -> None:
    with smart_cursor(commit=True) as (_, cur):
        cur.execute("SELECT value FROM smart_meta WHERE key = 'sample_history_plate_fix_v1'")
        migrated = cur.fetchone()
        if migrated and migrated['value'] == '1':
            return

        cur.execute(
            f"""
            SELECT sb.id, sb.number_plate, sb.qr_token, u.email
            FROM smart_bookings sb
            JOIN users u ON u.id = sb.user_id
            WHERE u.email IN ({','.join('?' for _ in SEEDED_SAMPLE_EMAILS)})
              AND sb.number_plate IN ({','.join('?' for _ in LEGACY_SAMPLE_PLATES)})
            ORDER BY sb.created_at ASC
            """,
            tuple(SEEDED_SAMPLE_EMAILS) + tuple(LEGACY_SAMPLE_PLATES),
        )
        rows = cur.fetchall()

        plate_map = {legacy: SAFE_SAMPLE_PLATES[index] for index, legacy in enumerate(LEGACY_SAMPLE_PLATES)}
        for row in rows:
            new_plate = plate_map.get(row['number_plate'])
            if not new_plate:
                continue
            payload = decode_token(row['qr_token'])
            payload['plate'] = new_plate
            new_token = create_token(payload)
            cur.execute(
                'UPDATE smart_bookings SET number_plate = ?, qr_token = ?, qr_svg = ?, updated_at = ? WHERE id = ?',
                (new_plate, new_token, _render_qr_svg(new_token), now_iso(), row['id']),
            )

        cur.execute("INSERT OR REPLACE INTO smart_meta (key, value) VALUES ('sample_history_plate_fix_v1', '1')")


def bootstrap_smart_backend() -> None:
    ensure_smart_schema()
    ensure_smart_users()
    seed_smart_slots()
    rebalance_smart_slots()
    enforce_smart_slot_limit(env_int('SMART_TOTAL_SLOTS', 100))
    seed_smart_history()
    migrate_seed_sample_plates()
    dedupe_live_bookings()


def fetch_weather(location: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    api_key = env_str('OPENWEATHER_API_KEY', '').strip()
    lat = float((location or {}).get('lat') or env_float('SYSTEM_LAT', 18.5204))
    lon = float((location or {}).get('lon') or env_float('SYSTEM_LON', 73.8567))
    cache_key = f'{lat:.4f}:{lon:.4f}'
    now_ts = int(time.time())

    with smart_cursor() as (_, cur):
        cur.execute('SELECT payload, expires_at FROM smart_weather_cache WHERE cache_key = ?', (cache_key,))
        row = cur.fetchone()
        if row and row['expires_at'] > now_ts:
            payload = parse_json(row['payload'], {})
            payload['fromCache'] = True
            return payload

    if not api_key:
        payload = {'category': 'normal', 'temperatureC': 28, 'description': 'clear', 'fetchedAt': now_iso(), 'fromCache': False}
    else:
        try:
            response = requests.get(
                'https://api.openweathermap.org/data/2.5/weather',
                params={'lat': lat, 'lon': lon, 'appid': api_key, 'units': 'metric'},
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            description = ((data.get('weather') or [{}])[0].get('main') or 'clear').lower()
            temp = float((data.get('main') or {}).get('temp') or 28)
            payload = {
                'category': 'rainy' if description in {'rain', 'drizzle', 'thunderstorm'} else 'hot' if temp >= 33 else 'normal',
                'temperatureC': temp,
                'description': description,
                'fetchedAt': now_iso(),
                'fromCache': False,
            }
        except Exception:
            payload = {'category': 'normal', 'temperatureC': 28, 'description': 'clear', 'fetchedAt': now_iso(), 'fromCache': False}

    with smart_cursor(commit=True) as (_, cur):
        cur.execute(
            'INSERT OR REPLACE INTO smart_weather_cache (cache_key, payload, expires_at) VALUES (?, ?, ?)',
            (cache_key, serialize_json(payload), now_ts + (env_int('WEATHER_CACHE_TTL_MS', 600000) // 1000)),
        )
    return payload


def slot_payload(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        '_id': row['id'],
        'label': row['label'],
        'zone': row['zone_name'],
        'floor': row['floor'],
        'row': row['row_index'],
        'column': row['column_index'],
        'covered': bool(row['covered']),
        'shaded': bool(row['shaded']),
        'nearLift': bool(row['near_lift']),
        'reservedFor': row['reserved_for'],
        'status': row['status'],
        'distanceToEntry': row['distance_to_entry'],
        'distanceToExit': row['distance_to_exit'],
    }


def fetch_smart_slots() -> Dict[str, Any]:
    with smart_cursor() as (_, cur):
        cur.execute('SELECT * FROM smart_slots ORDER BY zone_name, floor, row_index, column_index')
        rows = cur.fetchall()
    slots = [slot_payload(row) for row in rows]
    summary = {
        'total': len(slots),
        'free': sum(1 for slot in slots if slot['status'] == 'FREE'),
        'booked': sum(1 for slot in slots if slot['status'] == 'BOOKED'),
        'occupied': sum(1 for slot in slots if slot['status'] == 'OCCUPIED'),
    }
    summary['activeBookings'] = summary['booked'] + summary['occupied']
    summary['occupancyRate'] = round(((summary['booked'] + summary['occupied']) / summary['total']) * 100) if summary['total'] else 0
    return {'slots': slots, 'summary': summary}


def _floor_stats() -> Dict[int, Dict[str, int]]:
    with smart_cursor() as (_, cur):
        cur.execute('SELECT floor, status, COUNT(*) AS count FROM smart_slots GROUP BY floor, status')
        rows = cur.fetchall()
    result: Dict[int, Dict[str, int]] = {}
    for row in rows:
        stats = result.setdefault(row['floor'], {'total': 0, 'used': 0})
        stats['total'] += row['count']
        if row['status'] != 'FREE':
            stats['used'] += row['count']
    return result


def _navigation_for_slot(slot: Dict[str, Any]) -> Dict[str, Any]:
    variations = [
        ['Enter through main gate', 'Go straight for 20 meters', 'Take the first left', 'Continue to your slot'],
        ['Enter through main gate', 'Follow the blue lane', 'Take the ramp up', 'Turn right at the lift core'],
        ['Enter through east corridor', 'Keep left at the divider', 'Cross the sensor gate', 'Stop at the marked bay'],
        ['Enter through west lane', 'Go straight past junction J2', 'Take the second right', 'Arrive at your slot'],
        ['Enter through central aisle', 'Use the gentle ramp', 'Keep the fire exit on your left', 'Park at the highlighted slot'],
        ['Enter through express lane', 'Take the upper spiral ramp', 'Stay near the safety railing', 'Turn left into your bay'],
        ['Enter through south corridor', 'Proceed to junction J4', 'Take the lift-side lane', 'Your slot is ahead on the right'],
        ['Enter through north lane', 'Follow the overhead signs', 'Take the shaded aisle', 'Your slot is the next marked space'],
    ]
    route_index = (slot['floor'] + slot['row'] + slot['column'] + ord(slot['zone'][0])) % len(variations)
    steps = list(variations[route_index])
    steps.insert(2, f'Proceed to Level {slot["floor"]} in Zone {slot["zone"]}')
    steps.append(f'Park at Slot {slot["label"]}')
    return {'routeId': f'route-{route_index + 1}', 'steps': steps}


def _slot_score(slot: Dict[str, Any], floor_stats: Dict[int, Dict[str, int]], weather: Dict[str, Any], preference: Dict[str, Any], emergency_type: str) -> Dict[str, float]:
    floor_state = floor_stats.get(slot['floor'], {'total': 1, 'used': 0})
    occupancy_ratio = floor_state['used'] / max(floor_state['total'], 1)
    floor_score = (1 - occupancy_ratio) * 40 + max(0, 20 - slot['distanceToEntry']) + max(0, 20 - slot['distanceToExit'])
    if preference.get('nearLift') and slot['nearLift']:
        floor_score += 10
    if preference.get('covered') and slot['covered']:
        floor_score += 6
    if preference.get('shaded') and slot['shaded']:
        floor_score += 6

    score = floor_score
    if weather['category'] == 'rainy':
        score += 30 if slot['covered'] else -25
    elif weather['category'] == 'hot':
        score += 24 if slot['shaded'] else -10
    if preference.get('covered') and slot['covered']:
        score += 12
    if preference.get('shaded') and slot['shaded']:
        score += 12
    if preference.get('nearLift') and slot['nearLift']:
        score += 14
    if emergency_type != 'none':
        score += max(0, 50 - slot['distanceToExit'] * 3)
        if slot['reservedFor'] in {emergency_type, 'emergency'}:
            score += 50
    elif slot['reservedFor'] != 'none':
        score -= 40
    return {'floorScore': floor_score, 'slotScore': score}


def recommend_slot(vehicle_type: str = 'car', preference: Optional[Dict[str, Any]] = None, location: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    preference = preference or {}
    vehicle_type = vehicle_type if vehicle_type in SUPPORTED_VEHICLES else 'car'
    emergency_type = vehicle_type if vehicle_type in EMERGENCY_TYPES else 'none'
    weather = fetch_weather(location)
    all_slots = fetch_smart_slots()['slots']
    floor_stats = _floor_stats()
    candidates = []
    for slot in all_slots:
        if slot['status'] != 'FREE':
            continue
        if emergency_type == 'none' and slot['reservedFor'] != 'none':
            continue
        if emergency_type != 'none' and slot['reservedFor'] not in {'none', 'emergency', emergency_type}:
            continue
        scores = _slot_score(slot, floor_stats, weather, preference, emergency_type)
        candidates.append((scores['slotScore'], scores['floorScore'], slot))
    if not candidates:
        raise RuntimeError('No suitable slots available')
    candidates.sort(key=lambda item: item[0], reverse=True)
    slot_score, floor_score, best_slot = candidates[0]
    reason = (
        'Emergency vehicle prioritized near exit with reserved access.'
        if emergency_type != 'none'
        else 'Covered slot prioritized because rain is expected.'
        if weather['category'] == 'rainy'
        else 'Shaded slot prioritized because of high temperature.'
        if weather['category'] == 'hot'
        else 'Balanced floor occupancy, distance, and user preferences.'
    )
    return {
        'slot': best_slot,
        'weather': weather,
        'recommendation': {'reason': reason, 'floorScore': floor_score, 'slotScore': slot_score},
        'navigation': _navigation_for_slot(best_slot),
    }


def _render_qr_svg(token: str) -> str:
    bits = ''.join(format(byte, '08b') for byte in token.encode('utf-8'))[:21 * 21]
    bits = bits.ljust(21 * 21, '0')
    cell = 6
    rects = []
    for index, bit in enumerate(bits):
        if bit != '1':
            continue
        x = (index % 21) * cell
        y = (index // 21) * cell
        rects.append(f"<rect x='{x}' y='{y}' width='{cell}' height='{cell}' fill='#111827' />")
    safe_token = token.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {21 * cell} {21 * cell + 24}' role='img' aria-label='Booking QR'>"
        f"<rect width='{21 * cell}' height='{21 * cell + 24}' fill='#ffffff'/>"
        f"{''.join(rects)}"
        f"<text x='6' y='{21 * cell + 16}' font-size='8' fill='#374151'>"
        f"{safe_token[:18]}"
        f"</text></svg>"
    )


def _pricing_for_vehicle(vehicle_type: str) -> float:
    if vehicle_type in {'car', 'bike', 'truck'}:
        return float(fetch_legacy_pricing()[vehicle_type])
    return {
        'ambulance': env_float('PRICE_AMBULANCE', 0),
        'fire_truck': env_float('PRICE_FIRE_TRUCK', 0),
        'police': env_float('PRICE_POLICE', 0),
    }.get(vehicle_type, float(fetch_legacy_pricing()['car']))


def _fetch_booking_row(booking_id: str) -> Optional[sqlite3.Row]:
    with smart_cursor() as (_, cur):
        cur.execute('SELECT * FROM smart_bookings WHERE id = ?', (booking_id,))
        return cur.fetchone()


def fetch_booking_by_id(booking_id: str) -> Optional[Dict[str, Any]]:
    row = _fetch_booking_row(booking_id)
    return booking_payload(row) if row else None


def fetch_active_booking_by_plate(number_plate: str) -> Optional[Dict[str, Any]]:
    with smart_cursor() as (_, cur):
        cur.execute(
            """
            SELECT * FROM smart_bookings
            WHERE number_plate = ? AND status IN ('PENDING', 'PAYMENT_PENDING', 'ACTIVE')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (normalize_plate(number_plate),),
        )
        row = cur.fetchone()
    return booking_payload(row) if row else None


def fetch_gate_booking_by_plate(number_plate: str) -> Optional[Dict[str, Any]]:
    normalized_plate = normalize_plate(number_plate)
    with smart_cursor() as (_, cur):
        cur.execute(
            """
            SELECT *
            FROM smart_bookings
            WHERE number_plate = ? AND status IN ('PENDING', 'PAYMENT_PENDING', 'ACTIVE')
            ORDER BY
                CASE status
                    WHEN 'PENDING' THEN 0
                    WHEN 'PAYMENT_PENDING' THEN 1
                    WHEN 'ACTIVE' THEN 2
                    ELSE 3
                END,
                created_at DESC
            LIMIT 1
            """,
            (normalized_plate,),
        )
        row = cur.fetchone()
    return booking_payload(row) if row else None


def _fetch_live_booking_rows(number_plate: str) -> list[sqlite3.Row]:
    normalized_plate = normalize_plate(number_plate)
    with smart_cursor() as (_, cur):
        cur.execute(
            """
            SELECT *
            FROM smart_bookings
            WHERE number_plate = ? AND status IN ('PENDING', 'PAYMENT_PENDING', 'ACTIVE')
            ORDER BY
                CASE status
                    WHEN 'PENDING' THEN 0
                    WHEN 'PAYMENT_PENDING' THEN 1
                    WHEN 'ACTIVE' THEN 2
                    ELSE 3
                END,
                created_at DESC
            """,
            (normalized_plate,),
        )
        return cur.fetchall()


def _set_booking_terminal_state(booking_row: sqlite3.Row, status: str) -> None:
    now = now_iso()
    checked_out_at = now if status == 'COMPLETED' else booking_row['checked_out_at']
    with smart_cursor(commit=True) as (_, cur):
        cur.execute(
            'UPDATE smart_bookings SET status = ?, checked_out_at = ?, updated_at = ? WHERE id = ?',
            (status, checked_out_at, now, booking_row['id']),
        )
        cur.execute(
            'UPDATE smart_slots SET status = ?, current_booking_id = ?, last_updated = ? WHERE id = ?',
            ('FREE', None, now, booking_row['slot_id']),
        )


def dedupe_live_bookings() -> None:
    with smart_cursor() as (_, cur):
        cur.execute(
            """
            SELECT DISTINCT number_plate
            FROM smart_bookings
            WHERE number_plate IS NOT NULL
              AND number_plate != ''
              AND status IN ('PENDING', 'PAYMENT_PENDING', 'ACTIVE')
            """
        )
        plates = [row['number_plate'] for row in cur.fetchall()]

    for plate in plates:
        live_rows = _fetch_live_booking_rows(plate)
        if len(live_rows) <= 1:
            continue
        keeper = live_rows[0]
        for row in live_rows[1:]:
            terminal_status = 'EXPIRED' if row['status'] == 'PENDING' else 'COMPLETED'
            _set_booking_terminal_state(row, terminal_status)


def _fetch_slot_row(slot_id: str) -> Optional[sqlite3.Row]:
    with smart_cursor() as (_, cur):
        cur.execute('SELECT * FROM smart_slots WHERE id = ?', (slot_id,))
        return cur.fetchone()


def _user_payload(user_id: str) -> Optional[Dict[str, Any]]:
    from .database import get_user_by_id

    user = get_user_by_id(user_id)
    if not user:
        return None
    return {'_id': user['id'], 'name': user['name'], 'email': user['email'], 'phone': user.get('phone'), 'role': user['role']}


def booking_payload(row: sqlite3.Row) -> Dict[str, Any]:
    slot = _fetch_slot_row(row['slot_id'])
    payment_token = create_token({'booking_id': row['id'], 'purpose': 'payment'})
    payment_qr_svg = _render_qr_svg(payment_token)
    return {
        '_id': row['id'],
        'vehicleType': row['vehicle_type'],
        'emergencyType': row['emergency_type'],
        'numberPlate': row['number_plate'],
        'status': row['status'],
        'durationHours': row['duration_hours'],
        'startTime': row['start_time'],
        'endTime': row['end_time'],
        'price': row['price'],
        'bookingExpiresAt': row['booking_expires_at'],
        'checkedInAt': row['checked_in_at'],
        'checkedOutAt': row['checked_out_at'],
        'qrToken': row['qr_token'],
        'qrSvg': row['qr_svg'],
        'paymentReference': f'PAY-{row["id"][-8:].upper()}',
        'paymentToken': payment_token,
        'paymentQrSvg': payment_qr_svg,
        'paymentRequired': row['status'] == 'PAYMENT_PENDING',
        'recommendation': parse_json(row['recommendation_json'], {}),
        'weatherSnapshot': parse_json(row['weather_json'], {}),
        'navigation': parse_json(row['navigation_json'], {}),
        'slotId': {'_id': slot['id'], 'label': slot['label'], 'zone': slot['zone_name'], 'floor': slot['floor']} if slot else None,
        'userId': _user_payload(row['user_id']),
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


def _write_log(event_type: str, slot_id: Optional[str], booking_id: Optional[str], weather_category: Optional[str]) -> None:
    summary = fetch_smart_slots()['summary']
    slot = _fetch_slot_row(slot_id) if slot_id else None
    with smart_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            INSERT INTO smart_parking_logs (
                id, booking_id, slot_id, event_type, occupancy_level, total_slots, occupied_slots,
                zone_name, floor, weather_category, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                make_id('log'),
                booking_id,
                slot_id,
                event_type,
                summary['occupancyRate'],
                summary['total'],
                summary['booked'] + summary['occupied'],
                slot['zone_name'] if slot else None,
                slot['floor'] if slot else None,
                weather_category,
                now_iso(),
            ),
        )


def create_smart_booking(user_id: str, vehicle_type: str, duration_hours: int, number_plate: str, preference: Optional[Dict[str, Any]] = None, location: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    preference = preference or {}
    vehicle_type = vehicle_type if vehicle_type in SUPPORTED_VEHICLES else 'car'
    number_plate = normalize_plate(number_plate)
    duration_hours = max(1, int(duration_hours or 1))
    existing_live_booking = fetch_gate_booking_by_plate(number_plate) if number_plate else None
    if existing_live_booking and existing_live_booking['status'] in {'PENDING', 'PAYMENT_PENDING'}:
        return existing_live_booking
    if existing_live_booking and existing_live_booking['status'] == 'ACTIVE':
        raise RuntimeError('This vehicle already has an active parking session. Complete checkout before booking again.')
    recommendation = recommend_slot(vehicle_type, preference, location)
    booking_id = make_id('sbk')
    start_dt = utc_now()
    expires_at = start_dt + timedelta(minutes=env_int('BOOKING_GRACE_MINUTES', 10))
    end_dt = start_dt + timedelta(hours=duration_hours)
    qr_token = create_token({'booking_id': booking_id, 'plate': number_plate, 'type': vehicle_type})
    qr_svg = _render_qr_svg(qr_token)
    emergency_type = vehicle_type if vehicle_type in EMERGENCY_TYPES else 'none'
    now = now_iso()

    with smart_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            INSERT INTO smart_bookings (
                id, user_id, slot_id, vehicle_type, emergency_type, number_plate, location_json,
                preference_json, weather_json, status, booking_expires_at, checked_in_at, checked_out_at,
                start_time, end_time, duration_hours, price, qr_token, qr_svg, recommendation_json,
                navigation_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                booking_id,
                user_id,
                recommendation['slot']['_id'],
                vehicle_type,
                emergency_type,
                number_plate,
                serialize_json(location or {}),
                serialize_json(preference),
                serialize_json(recommendation['weather']),
                'PENDING',
                expires_at.isoformat(),
                None,
                None,
                start_dt.isoformat(),
                end_dt.isoformat(),
                duration_hours,
                _pricing_for_vehicle(vehicle_type) * duration_hours,
                qr_token,
                qr_svg,
                serialize_json(recommendation['recommendation']),
                serialize_json(recommendation['navigation']),
                now,
                now,
            ),
        )
        cur.execute('UPDATE smart_slots SET status = ?, current_booking_id = ?, last_updated = ? WHERE id = ?', ('BOOKED', booking_id, now, recommendation['slot']['_id']))

    row = _fetch_booking_row(booking_id)
    _write_log('BOOKED', recommendation['slot']['_id'], booking_id, recommendation['weather']['category'])
    payload = booking_payload(row)
    emit_app_event('booking:created', {'booking': payload})
    return payload


def fetch_user_bookings(user_id: str) -> list[Dict[str, Any]]:
    with smart_cursor() as (_, cur):
        cur.execute('SELECT * FROM smart_bookings WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        rows = cur.fetchall()
    return [booking_payload(row) for row in rows]


def fetch_all_smart_bookings(limit: int = 100) -> list[Dict[str, Any]]:
    with smart_cursor() as (_, cur):
        cur.execute('SELECT * FROM smart_bookings ORDER BY created_at DESC LIMIT ?', (limit,))
        rows = cur.fetchall()
    return [booking_payload(row) for row in rows]


def update_smart_booking_status(booking_id: str, status: str) -> Dict[str, Any]:
    row = _fetch_booking_row(booking_id)
    if not row:
        raise RuntimeError('Booking not found')

    now = now_iso()
    checked_in_at = now if status == 'ACTIVE' and not row['checked_in_at'] else row['checked_in_at']
    checked_out_at = now if status in {'COMPLETED', 'EXPIRED'} else row['checked_out_at']

    if status == 'ACTIVE':
        slot_status = 'OCCUPIED'
        current_booking_id = booking_id
        event_type = 'CHECKED_IN'
        app_event = 'booking:checked-in'
    elif status in {'PENDING', 'PAYMENT_PENDING'}:
        slot_status = 'BOOKED'
        current_booking_id = booking_id
        event_type = 'BOOKED' if status == 'PENDING' else 'PAYMENT_REQUESTED'
        app_event = 'booking:created' if status == 'PENDING' else 'payment:required'
    else:
        slot_status = 'FREE'
        current_booking_id = None
        event_type = 'EXPIRED' if status == 'EXPIRED' else 'CHECKED_OUT'
        app_event = 'booking:expired' if status == 'EXPIRED' else 'booking:checked-out'

    with smart_cursor(commit=True) as (_, cur):
        cur.execute(
            'UPDATE smart_bookings SET status = ?, checked_in_at = ?, checked_out_at = ?, updated_at = ? WHERE id = ?',
            (status, checked_in_at, checked_out_at, now, booking_id),
        )
        cur.execute(
            'UPDATE smart_slots SET status = ?, current_booking_id = ?, last_updated = ? WHERE id = ?',
            (slot_status, current_booking_id, now, row['slot_id']),
        )

    updated = booking_payload(_fetch_booking_row(booking_id))
    _write_log(event_type, row['slot_id'], booking_id, updated['weatherSnapshot'].get('category'))
    emit_app_event(app_event, {'booking': updated})
    return updated


def expire_pending_bookings() -> int:
    now_value = utc_now().isoformat()
    with smart_cursor() as (_, cur):
        cur.execute(
            """
            SELECT id FROM smart_bookings
            WHERE status IN ('PENDING', 'PAYMENT_PENDING')
              AND booking_expires_at IS NOT NULL
              AND booking_expires_at <= ?
            """,
            (now_value,),
        )
        rows = cur.fetchall()
    for row in rows:
        booking = _fetch_booking_row(row['id'])
        if not booking:
            continue
        update_smart_booking_status(row['id'], 'EXPIRED')
    return len(rows)


def start_expiry_worker() -> None:
    global EXPIRY_THREAD_STARTED
    if EXPIRY_THREAD_STARTED:
        return
    EXPIRY_THREAD_STARTED = True

    def loop() -> None:
        while True:
            try:
                expire_pending_bookings()
            except Exception:
                pass
            time.sleep(60)

    threading.Thread(target=loop, daemon=True).start()


def validate_qr_token(qr_token: str) -> Dict[str, Any]:
    payload = decode_token(qr_token)
    booking_id = payload.get('booking_id')
    row = _fetch_booking_row(booking_id)
    if not row or row['status'] not in {'PENDING', 'PAYMENT_PENDING', 'ACTIVE'}:
        raise RuntimeError('Booking is not valid for gate entry')
    booking = booking_payload(row)
    if row['status'] in {'PENDING', 'PAYMENT_PENDING'}:
        booking = update_smart_booking_status(row['id'], 'ACTIVE')
    emit_app_event('gate:opened', {'booking': booking, 'gate': 'OPEN'})
    return {'success': True, 'gate': 'OPEN', 'booking': booking, 'message': 'QR validated. Gate opened.'}


def request_booking_payment(booking_id: str) -> Dict[str, Any]:
    booking = fetch_booking_by_id(booking_id)
    if not booking:
        raise RuntimeError('Booking not found')
    if booking['status'] == 'ACTIVE':
        return booking
    if booking['status'] == 'COMPLETED':
        raise RuntimeError('This parking session is already completed.')
    if booking['status'] == 'EXPIRED':
        raise RuntimeError('This booking has expired.')
    return update_smart_booking_status(booking_id, 'ACTIVE')


def update_iot_slot(slot_code: str, status: str) -> Dict[str, Any]:
    normalized_status = (status or 'FREE').upper()
    if normalized_status not in {'FREE', 'BOOKED', 'OCCUPIED', 'BLOCKED'}:
        normalized_status = 'FREE'
    with smart_cursor(commit=True) as (_, cur):
        cur.execute('UPDATE smart_slots SET status = ?, last_updated = ? WHERE code = ?', (normalized_status, now_iso(), slot_code))
        cur.execute('SELECT * FROM smart_slots WHERE code = ?', (slot_code,))
        row = cur.fetchone()
    if not row:
        raise RuntimeError('Slot not found')
    payload = slot_payload(row)
    _write_log('IOT_UPDATE', row['id'], None, None)
    emit_app_event('slot:updated', {'slot': payload})
    return payload


def fetch_predictions() -> Dict[str, Any]:
    with smart_cursor() as (_, cur):
        cur.execute('SELECT occupancy_level, created_at FROM smart_parking_logs ORDER BY created_at DESC LIMIT 96')
        rows = cur.fetchall()
    if not rows:
        return {'projectedOccupancy': 0, 'likelyFullInMinutes': None, 'bestArrivalTime': '6:30 PM', 'confidence': 'low'}
    occupancy = [row['occupancy_level'] for row in rows]
    projected = round(sum(occupancy) / len(occupancy))
    trend = occupancy[0] - occupancy[-1] if len(occupancy) > 1 else 0
    likely_full = max(10, int((100 - occupancy[0]) / max(trend, 1) * 10)) if trend > 0 else None
    return {'projectedOccupancy': projected, 'likelyFullInMinutes': likely_full, 'bestArrivalTime': '6:30 PM' if projected > 80 else 'Now', 'confidence': 'medium' if len(rows) >= 24 else 'low'}


def fetch_context() -> Dict[str, Any]:
    now = datetime.now()
    return {
        'success': True,
        'environment': {'isNight': now.hour >= 19 or now.hour < 6, 'lightingMode': 'HIGH' if now.hour >= 19 or now.hour < 6 else 'NORMAL'},
        'summary': fetch_smart_slots()['summary'],
        'predictions': fetch_predictions(),
    }


def fetch_admin_overview() -> Dict[str, Any]:
    bookings = fetch_all_smart_bookings(100)
    snapshot = fetch_smart_slots()
    slots = snapshot['slots']
    summary = snapshot['summary']
    predictions = fetch_predictions()
    profit = round(
        sum(
            float(booking.get('price') or 0)
            for booking in bookings
            if booking.get('status') in {'ACTIVE', 'COMPLETED'}
        ),
        2,
    )
    floor_map: Dict[int, Dict[str, int]] = {}
    zone_map: Dict[str, Dict[str, int]] = {}
    for slot in slots:
        floor_item = floor_map.setdefault(slot['floor'], {'total': 0, 'used': 0})
        floor_item['total'] += 1
        if slot['status'] != 'FREE':
            floor_item['used'] += 1
        zone_item = zone_map.setdefault(slot['zone'], {'total': 0, 'used': 0})
        zone_item['total'] += 1
        if slot['status'] != 'FREE':
            zone_item['used'] += 1

    with smart_cursor() as (_, cur):
        cur.execute('SELECT occupancy_level, created_at FROM smart_parking_logs ORDER BY created_at DESC LIMIT 168')
        logs = cur.fetchall()

    peak_hours = []
    for hour in range(24):
        values = [row['occupancy_level'] for row in logs if datetime.fromisoformat(row['created_at']).hour == hour]
        peak_hours.append({'hour': f'{hour:02d}:00', 'occupancy': round(sum(values) / len(values)) if values else 0})

    now = datetime.now()
    return {
        'success': True,
        'summary': summary,
        'profit': profit,
        'environment': {'isNight': now.hour >= 19 or now.hour < 6, 'lightingMode': 'HIGH' if now.hour >= 19 or now.hour < 6 else 'NORMAL'},
        'bookings': bookings,
        'peakHours': peak_hours,
        'floorLoad': [{'floor': floor, 'occupancy': round((values['used'] / values['total']) * 100) if values['total'] else 0} for floor, values in sorted(floor_map.items())],
        'zoneLoad': [{'zone': zone, 'occupancy': round((values['used'] / values['total']) * 100) if values['total'] else 0} for zone, values in sorted(zone_map.items())],
        'predictions': predictions,
    }
