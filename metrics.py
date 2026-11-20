"""E2 KPIs, computed live from the database (plus offline model metrics from metrics.json)."""

import json

from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import LATE_THRESHOLD_MINUTES, METRICS_PATH, SIM_START
from app.core.models import AuditLog
from app.e2.models import DockAppointment, DockDoor, Shipment


def _model_metrics() -> dict:
    if METRICS_PATH.exists():
        return json.loads(METRICS_PATH.read_text()).get("e2", {})
    return {}


def e2_metrics(db: Session) -> dict:
    now = clock.now(db)
    shipments = db.query(Shipment).all()
    arrived = [s for s in shipments if s.yard_arrived_at]
    on_time = [s for s in arrived
               if (s.yard_arrived_at - s.scheduled_arrival).total_seconds() / 60 <= LATE_THRESHOLD_MINUTES]

    doors = db.query(DockDoor).filter(DockDoor.status != "MAINTENANCE").count() or 1
    elapsed_min = max(1.0, (now - SIM_START).total_seconds() / 60)
    used_min = 0.0
    for a in db.query(DockAppointment).filter(DockAppointment.status.in_(("ACTIVE", "COMPLETED"))):
        end = min(a.scheduled_end, now)
        used_min += max(0.0, (end - max(a.scheduled_start, SIM_START)).total_seconds() / 60)

    reassignments = db.query(AuditLog).filter(AuditLog.module == "e2", AuditLog.action == "DOCK_RELEASED").count()
    by_status: dict[str, int] = {}
    for s in shipments:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    return {
        "shipments_total": len(shipments),
        "by_status": by_status,
        "arrived": len(arrived),
        "on_time_arrival_pct": round(100 * len(on_time) / len(arrived), 1) if arrived else None,
        "dock_utilisation_pct": round(100 * used_min / (doors * elapsed_min), 1),
        "reassignments_triggered": reassignments,
        "high_risk_now": sum(1 for s in shipments if s.delay_band == "HIGH" and s.status == "IN_TRANSIT"),
        "delay_model": _model_metrics().get("delay_model", {}),
    }
