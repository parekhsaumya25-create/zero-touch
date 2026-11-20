"""
In-process event bus — the ONLY channel between E2 and PR2.

E2 and PR2 publish domain events ("purchase_order.PO_CREATED",
"shipment.UNLOADED", ...). Neither knows who listens. The cross-module wiring
lives in app/core/integration.py, which subscribes to both.

Synchronous and in the publisher's DB transaction: simple, deterministic, and
an event is never "lost" between modules. Swapping to Redis/Kafka later means
replacing publish()/subscribe() — no change to E2 or PR2.
"""

import json
import logging
from collections import defaultdict
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit import raise_alert
from app.core.models import EventLog

log = logging.getLogger("zerotouch")

Handler = Callable[[Session, dict], None]
_subscribers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event_name: str, handler: Handler) -> None:
    if handler not in _subscribers[event_name]:
        _subscribers[event_name].append(handler)


def clear_subscribers() -> None:
    _subscribers.clear()


def publish(db: Session, event_name: str, payload: dict) -> None:
    handlers = list(_subscribers.get(event_name, []))
    row = EventLog(
        name=event_name,
        payload_json=json.dumps(payload, default=str),
        handled_by=", ".join(h.__name__ for h in handlers),
        created_at=clock.now(db),
    )
    db.add(row)
    db.flush()
    for handler in handlers:
        # A failing subscriber must not roll back the publisher's own work, so
        # each handler runs in a SAVEPOINT; on failure we raise an alert.
        nested = db.begin_nested()
        try:
            handler(db, payload)
            nested.commit()
        except Exception as exc:  # noqa: BLE001
            nested.rollback()
            log.exception("event handler %s failed for %s", handler.__name__, event_name)
            raise_alert(db, "core", "event", row.id, "HANDLER_FAILED", "CRITICAL",
                        f"{handler.__name__} failed on {event_name}: {exc}")
