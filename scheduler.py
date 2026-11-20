"""
Background ticker (APScheduler). Each tick: advance the simulated clock, then
run every registered tick handler (E2 registers truck movement + yard logic).
Core never imports E2 — modules register themselves.
"""

import logging
import threading
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import SIM_MINUTES_PER_TICK, TICK_SECONDS
from app.core.database import session_scope

log = logging.getLogger("zerotouch")

TickHandler = Callable[[Session, float], None]
_handlers: list[TickHandler] = []
_scheduler: BackgroundScheduler | None = None
# Held during a tick and during demo reset so the two never interleave.
lock = threading.RLock()


def register_tick(handler: TickHandler) -> None:
    if handler not in _handlers:
        _handlers.append(handler)


def run_tick(db: Session, minutes: float = SIM_MINUTES_PER_TICK) -> None:
    """One simulation step. Used by the scheduler AND by demo scripts (fast-forward)."""
    clock.advance(db, minutes)
    for handler in _handlers:
        handler(db, minutes)


def _job() -> None:
    if not lock.acquire(blocking=False):
        return  # reset in progress; skip this tick
    try:
        with session_scope() as db:
            run_tick(db)
    except Exception:  # noqa: BLE001
        log.exception("tick failed")
    finally:
        lock.release()


def start() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_job, "interval", seconds=TICK_SECONDS, id="sim_tick", max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("simulation scheduler started: %ss real = %s sim-min", TICK_SECONDS, SIM_MINUTES_PER_TICK)


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def is_running() -> bool:
    return bool(_scheduler and _scheduler.running)
