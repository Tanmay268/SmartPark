from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional

_event_lock = Lock()
latest_gate_progress: Optional[Dict[str, Any]] = None
latest_gate_event: Optional[Dict[str, Any]] = None
latest_app_event: Optional[Dict[str, Any]] = None
state_version = 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_progress(payload: Dict[str, Any]) -> None:
    global latest_gate_progress, state_version
    with _event_lock:
        latest_gate_progress = {**payload, 'timestamp': now_iso()}
        state_version += 1


def emit_gate_event(payload: Dict[str, Any]) -> None:
    global latest_gate_event, state_version
    with _event_lock:
        latest_gate_event = {**payload, 'receivedAt': now_iso()}
        state_version += 1


def emit_app_event(event_type: str, payload: Dict[str, Any]) -> None:
    global latest_app_event, state_version
    with _event_lock:
        latest_app_event = {
            'type': event_type,
            'payload': payload,
            'receivedAt': now_iso(),
        }
        state_version += 1


def get_latest_events() -> Dict[str, Any]:
    with _event_lock:
        return {
            'gateProgress': latest_gate_progress,
            'gateEvent': latest_gate_event,
            'appEvent': latest_app_event,
            'stateVersion': state_version,
        }
