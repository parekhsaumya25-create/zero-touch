from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core import clock
from app.core.database import get_db
from app.core.models import AuditLog
from app.core.scheduler import lock
from app.e2.models import DockAppointment, DockDoor, Shipment
from app.e2.schemas import AppointmentOut, DoorOut, ShipmentCreate, ShipmentOut
from app.e2.services import dock, geo, metrics, tracking

router = APIRouter(prefix="/api/e2", tags=["E2 — Yard & Delivery"])
# Aliases matching the original spec paths.
alias = APIRouter(prefix="/api/shipments", tags=["E2 — Yard & Delivery"])

ACTIVE = ("IN_TRANSIT", "IN_YARD", "AT_DOCK")


def to_out(db: Session, s: Shipment) -> ShipmentOut:
    out = ShipmentOut.model_validate({
        **{c.name: getattr(s, c.name) for c in Shipment.__table__.columns},
        "po_note": f"Linked to {s.po_ref}" if s.po_ref else "No linked PO — this shipment was created directly.",
    })
    appt = dock.current_appointment(db, s) or (
        db.query(DockAppointment).filter(DockAppointment.shipment_id == s.id).order_by(DockAppointment.id.desc()).first()
    )
    if appt:
        door = db.get(DockDoor, appt.door_id)
        out.appointment = AppointmentOut.model_validate({**appt.__dict__, "door_no": door.door_no if door else None})
    return out


def _get(db: Session, ref: str) -> Shipment:
    s = db.query(Shipment).filter((Shipment.shipment_ref == ref) | (Shipment.tracking_no == ref)).first()
    if not s:
        raise HTTPException(404, f"shipment {ref} not found")
    return s


@router.post("/shipments", response_model=ShipmentOut)
def create_shipment(body: ShipmentCreate, db: Session = Depends(get_db)):
    """E2 standalone entry point: a shipment with no PO behind it (po_ref may be null)."""
    with lock:
        s = tracking.create_shipment(
            db, route_id=body.route_id, load_type=body.load_type, priority=body.priority,
            trailer_id=body.trailer_id, scheduled_arrival=body.scheduled_arrival, po_ref=body.po_ref,
            expected_qty=body.expected_qty, start_progress=body.start_progress,
        )
        db.commit()
    return to_out(db, s)


@router.get("/shipments", response_model=list[ShipmentOut])
def list_shipments(db: Session = Depends(get_db)):
    return [to_out(db, s) for s in db.query(Shipment).order_by(Shipment.id).all()]


@router.get("/shipments/live", response_model=list[ShipmentOut])
@alias.get("/live", response_model=list[ShipmentOut])
def live(db: Session = Depends(get_db)):
    rows = db.query(Shipment).filter(Shipment.status.in_(ACTIVE)).order_by(Shipment.id).all()
    return [to_out(db, s) for s in rows]


@router.get("/shipments/search", response_model=list[ShipmentOut])
@alias.get("/search", response_model=list[ShipmentOut])
def search(q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """Matches tracking_no OR trailer_id OR shipment ref (or PO ref)."""
    return [to_out(db, s) for s in tracking.search(db, q)]


@router.get("/shipments/{ref}")
def shipment_detail(ref: str, db: Session = Depends(get_db)):
    s = _get(db, ref)
    timeline = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == s.shipment_ref, AuditLog.module == "e2")
        .order_by(AuditLog.id)
        .all()
    )
    appts = db.query(DockAppointment).filter(DockAppointment.shipment_id == s.id).order_by(DockAppointment.id).all()
    return {
        "shipment": to_out(db, s),
        "route": {k: v for k, v in geo.get_route(s.route_id).items() if k in ("coords", "origin", "destination")},
        "appointments": [
            {**AppointmentOut.model_validate({**a.__dict__, "door_no": db.get(DockDoor, a.door_id).door_no}).model_dump()}
            for a in appts
        ],
        "timeline": [
            {"at": t.created_at, "action": t.action, "reason": t.reason, "decided_by": t.decided_by,
             "confidence": t.confidence}
            for t in timeline
        ],
    }


@router.post("/shipments/{ref}/disrupt", response_model=ShipmentOut)
def disrupt(ref: str, db: Session = Depends(get_db)):
    """Demo control: simulate a traffic jam (from the teammate prototype)."""
    with lock:
        s = _get(db, ref)
        try:
            tracking.disrupt(db, s)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        db.commit()
    return to_out(db, s)


@router.post("/shipments/{ref}/clear", response_model=ShipmentOut)
def clear(ref: str, db: Session = Depends(get_db)):
    with lock:
        s = _get(db, ref)
        if s.disrupted:
            tracking.clear_disruption(db, s)
        db.commit()
    return to_out(db, s)


@router.get("/routes")
def routes():
    data = geo.load_routes()
    return {
        "dc": data["dc"],
        "routes": [
            {"route_id": rid, "origin": r["origin"], "length_km": round(r["length_km"], 1), "coords": r["coords"]}
            for rid, r in data["routes"].items()
        ],
    }


@router.get("/dock", response_model=list[DoorOut])
def dock_board(db: Session = Depends(get_db)):
    now = clock.now(db)
    out = []
    for d in db.query(DockDoor).order_by(DockDoor.door_no).all():
        appts = (
            db.query(DockAppointment)
            .filter(DockAppointment.door_id == d.id, DockAppointment.status.in_(("BOOKED", "ACTIVE")))
            .order_by(DockAppointment.scheduled_start)
            .all()
        )

        def fmt(a):
            s = db.get(Shipment, a.shipment_id)
            return {"shipment_ref": s.shipment_ref, "po_ref": s.po_ref, "load_type": s.load_type,
                    "priority": s.priority, "start": a.scheduled_start, "end": a.scheduled_end,
                    "status": a.status, "reason": a.assigned_reason}

        current = next((fmt(a) for a in appts if a.status == "ACTIVE"), None)
        upcoming = [fmt(a) for a in appts if a.status == "BOOKED" and a.scheduled_end > now][:4]
        out.append(DoorOut(door_no=d.door_no, zone=d.zone, compatible_types=d.types, status=d.status,
                           current=current, upcoming=upcoming))
    return out


@router.get("/yard", response_model=list[ShipmentOut])
def yard(db: Session = Depends(get_db)):
    """Yard queue in service order: priority, then arrival time."""
    return [to_out(db, s) for s in dock.yard_queue(db)]


@router.get("/metrics")
def e2_metrics(db: Session = Depends(get_db)):
    return metrics.e2_metrics(db)
