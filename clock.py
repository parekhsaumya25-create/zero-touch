"""
Simulated clock + system mode, persisted in SystemState so the API process,
scheduler thread and demo scripts all agree on "now".
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import SIM_START
from app.core.models import SystemState

MODES = ("e2", "pr2", "full")


def _get(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(SystemState, key)
    return row.value if row else default


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(SystemState, key)
    if row:
        row.value = value
    else:
        db.add(SystemState(key=key, value=value))
    db.flush()


def now(db: Session) -> datetime:
    raw = _get(db, "sim_now")
    return datetime.fromisoformat(raw) if raw else SIM_START


def set_now(db: Session, value: datetime) -> None:
    _set(db, "sim_now", value.isoformat())


def advance(db: Session, minutes: float) -> datetime:
    new = now(db) + timedelta(minutes=minutes)
    set_now(db, new)
    return new


def get_mode(db: Session) -> str:
    return _get(db, "mode", "full")


def set_mode(db: Session, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    _set(db, "mode", mode)


def next_seq(db: Session, name: str) -> int:
    """Monotonic counters for human-readable refs (PO-2026-0001, SHP-1001...)."""
    key = f"seq:{name}"
    value = int(_get(db, key, "0")) + 1
    _set(db, key, str(value))
    return value
