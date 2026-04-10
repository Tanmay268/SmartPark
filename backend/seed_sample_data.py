from python_backend.database import ensure_schema, seed_data
from python_backend.smart_backend import (
    bootstrap_smart_backend,
    now_iso,
    rebalance_smart_slots,
    seed_smart_history,
    smart_cursor,
)


def reset_sample_history() -> None:
    with smart_cursor(commit=True) as (_, cur):
        cur.execute('UPDATE smart_slots SET status = ?, current_booking_id = NULL, last_updated = ?', ('FREE', now_iso()))
        cur.execute('DELETE FROM smart_bookings')
        cur.execute('DELETE FROM smart_parking_logs')
        cur.execute("DELETE FROM smart_meta WHERE key IN ('sample_history_seeded', 'sample_history_plate_fix_v1')")


def main() -> None:
    ensure_schema()
    seed_data()
    bootstrap_smart_backend()
    rebalance_smart_slots(total_slots=100, floors=4)
    reset_sample_history()
    seed_smart_history()
    print('Seeded 100 smart slots across 4 floors and regenerated sample bookings/logs.')


if __name__ == '__main__':
    main()
