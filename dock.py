"""
Dock-door assignment (greedy, explainable).

Eligible door = not in maintenance AND compatible with the load type AND no
overlapping BOOKED/ACTIVE appointment in the slot (overlap is a HARD conflict:
one truck per door per slot). Among eligible (door, earliest-slot) pairs we score:
  window          — how close the slot is to the truck's ETA (waiting penalised
                    more for HIGH-priority loads)
  zone            — door is in the zone nearest the goods' storage area
  specialisation  — prefer single-purpose doors, keep flexible doors free
If nothing is free when a truck is physically in the yard, it waits in a yard
queue ordered by priority, then arrival time.

Every decision stores `assigned_reason` in plain English.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit import audit, raise_alert
from app.core.config import (
    DOCK_SCORE_WEIGHTS,
    DOCK_SEARCH_HORIZON_HOURS,
    DOCK_SEARCH_STEP_MINUTES,
    DOCK_SLOT_MINUTES,
    LOAD_ZONE,
    PRIORITY_RANK,
    PRIORITY_WAIT_PENALTY,
)
from app.core.state_machine import transition
from app.e2.models import DockAppointment, DockDoor, Shipment

ACTIVE_APPT = ("BOOKED", "ACTIVE")
SLOT = timedelta(minutes=DOCK_SLOT_MINUTES)


def _t(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _round_up(dt: datetime, minutes: int = DOCK_SEARCH_STEP_MINUTES) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    extra = (-dt.minute) % minutes
    return dt + timedelta(minutes=extra)


def conflicts(db: Session, door_id: int, start: datetime, end: datetime, exclude_shipment_id: int | None = None):
    q = db.query(DockAppointment).filter(
        DockAppointment.door_id == door_id,
        DockAppointment.status.in_(ACTIVE_APPT),
        DockAppointment.scheduled_start < end,
        DockAppointment.scheduled_end > start,
    )
    if exclude_shipment_id is not None:
        q = q.filter(DockAppointment.shipment_id != exclude_shipment_id)
    return q.all()


def current_appointment(db: Session, shipment: Shipment) -> DockAppointment | None:
    return (
        db.query(DockAppointment)
        .filter(DockAppointment.shipment_id == shipment.id, DockAppointment.status.in_(ACTIVE_APPT))
        .order_by(DockAppointment.id.desc())
        .first()
    )


def _ref(db: Session, shipment_id: int) -> str:
    s = db.get(Shipment, shipment_id)
    return s.shipment_ref if s else f"#{shipment_id}"


def _score(shipment: Shipment, door: DockDoor, wait_min: float) -> dict:
    penalty = PRIORITY_WAIT_PENALTY.get(shipment.priority, 1.0)
    window = max(0.0, 1.0 - penalty * min(wait_min, 240) / 240)
    zone = 1.0 if door.zone == LOAD_ZONE.get(shipment.load_type) else 0.0
    specialisation = 1.0 - (len(door.types) - 1) / 3
    w = DOCK_SCORE_WEIGHTS
    total = w["window"] * window + w["zone"] * zone + w["specialisation"] * specialisation
    return {"window": round(window, 3), "zone": zone, "specialisation": round(specialisation, 3), "total": round(total, 3)}


def find_best_slot(db: Session, shipment: Shipment, desired_start: datetime):
    """Returns (door, start, score, considered_notes) or (None, None, None, notes)."""
    desired_start = _round_up(desired_start)
    steps = int(DOCK_SEARCH_HORIZON_HOURS * 60 / DOCK_SEARCH_STEP_MINUTES)
    notes: list[str] = []
    incompatible: list[str] = []
    candidates = []
    for door in db.query(DockDoor).order_by(DockDoor.door_no).all():
        if door.status == "MAINTENANCE":
            notes.append(f"{door.door_no} under maintenance")
            continue
        if shipment.load_type not in door.types:
            incompatible.append(door.door_no)
            continue
        first_conflict = None
        for i in range(steps + 1):
            start = desired_start + timedelta(minutes=i * DOCK_SEARCH_STEP_MINUTES)
            clash = conflicts(db, door.id, start, start + SLOT, exclude_shipment_id=shipment.id)
            if not clash:
                wait = (start - desired_start).total_seconds() / 60
                candidates.append((door, start, _score(shipment, door, wait)))
                if first_conflict:
                    notes.append(
                        f"{door.door_no} conflict: booked {_t(first_conflict.scheduled_start)}–"
                        f"{_t(first_conflict.scheduled_end)} by {_ref(db, first_conflict.shipment_id)}, "
                        f"earliest free {_t(start)}"
                    )
                break
            first_conflict = first_conflict or clash[0]
        else:
            notes.append(f"{door.door_no} fully booked for the next {DOCK_SEARCH_HORIZON_HOURS}h")
    if incompatible:
        notes.append(f"{len(incompatible)} doors can't take {shipment.load_type} ({', '.join(incompatible)})")
    if not candidates:
        return None, None, None, notes
    candidates.sort(key=lambda c: (-c[2]["total"], c[1], c[0].door_no))
    door, start, score = candidates[0]
    return door, start, score, notes


def book(db: Session, shipment: Shipment, desired_start: datetime, context: str = "") -> DockAppointment | None:
    """Pre-book a dock slot around the ETA (called at dispatch and on reassignment)."""
    door, start, score, notes = find_best_slot(db, shipment, desired_start)
    now = clock.now(db)
    if door is None:
        msg = f"No {shipment.load_type}-capable door free within {DOCK_SEARCH_HORIZON_HOURS}h of ETA {_t(desired_start)}."
        raise_alert(db, "e2", "shipment", shipment.shipment_ref, "NO_DOCK_SLOT", "WARN", msg)
        audit(db, "e2", "shipment", shipment.shipment_ref, "DOCK_UNASSIGNED", "rules:dock_greedy", msg)
        return None
    wait = int((start - _round_up(desired_start)).total_seconds() / 60)
    zone_txt = f"zone {door.zone} is nearest {shipment.load_type} storage" if score["zone"] else f"zone {door.zone} (not the preferred zone, best available)"
    reason = (
        f"{context}{door.door_no} booked {_t(start)}–{_t(start + SLOT)} for {shipment.priority}-priority "
        f"{shipment.load_type} load (ETA {_t(desired_start)}"
        + (f", waits {wait} min for the first conflict-free slot" if wait else ", slot matches ETA")
        + f"); {zone_txt}; score {score['total']:.2f}."
    )
    if notes:
        reason += " Considered: " + "; ".join(notes) + "."
    appt = DockAppointment(
        shipment_id=shipment.id, door_id=door.id, scheduled_start=start, scheduled_end=start + SLOT,
        assigned_reason=reason, status="BOOKED", created_at=now,
    )
    db.add(appt)
    db.flush()
    audit(db, "e2", "dock_appointment", shipment.shipment_ref, "DOCK_BOOKED", "rules:dock_greedy", reason, score["total"])
    return appt


def release(db: Session, appt: DockAppointment, reason: str) -> None:
    appt.status = "RELEASED"
    door = db.get(DockDoor, appt.door_id)
    audit(db, "e2", "dock_appointment", _ref(db, appt.shipment_id), "DOCK_RELEASED", "rules:delay_threshold",
          f"{door.door_no} {_t(appt.scheduled_start)}–{_t(appt.scheduled_end)} released: {reason}")
    db.flush()


def try_dock_now(db: Session, shipment: Shipment) -> bool:
    """Truck is physically in the yard: put it on a door now if one is free, else it waits in the queue."""
    now = clock.now(db)
    appt = current_appointment(db, shipment)
    ordered_doors = []
    if appt:
        ordered_doors.append(db.get(DockDoor, appt.door_id))
    ordered_doors += [d for d in db.query(DockDoor).order_by(DockDoor.door_no).all() if not appt or d.id != appt.door_id]

    for door in ordered_doors:
        if door.status != "AVAILABLE" or shipment.load_type not in door.types:
            continue
        clash = conflicts(db, door.id, now, now + SLOT, exclude_shipment_id=shipment.id)
        if clash:
            continue  # someone else holds this door in the next hour — hard conflict
        own = appt is not None and appt.door_id == door.id
        if appt and not own:
            appt.status = "RELEASED"
        reason = (
            f"Docked at {door.door_no} on yard arrival ({_t(now)}): "
            + ("its booked door was free." if own else
               f"door free and no booking conflicts until {_t(now + SLOT)}"
               + (f" (original booking {db.get(DockDoor, appt.door_id).door_no} released)." if appt else "."))
        )
        if own:
            appt.status = "ACTIVE"
            appt.scheduled_start, appt.scheduled_end = now, now + SLOT
            appt.assigned_reason = appt.assigned_reason + " | " + reason
        else:
            appt = DockAppointment(shipment_id=shipment.id, door_id=door.id, scheduled_start=now,
                                   scheduled_end=now + SLOT, assigned_reason=reason, status="ACTIVE", created_at=now)
            db.add(appt)
        door.status = "OCCUPIED"
        shipment.docked_at = now
        db.flush()
        transition(db, shipment, "shipment", "AT_DOCK", module="e2", decided_by="rules:dock_greedy",
                   reason=reason, entity_id=shipment.shipment_ref,
                   payload={"po_ref": shipment.po_ref, "shipment_ref": shipment.shipment_ref, "door": door.door_no})
        return True
    return False


def yard_queue(db: Session) -> list[Shipment]:
    waiting = db.query(Shipment).filter(Shipment.status == "IN_YARD").all()
    return sorted(waiting, key=lambda s: (PRIORITY_RANK.get(s.priority, 9), s.yard_arrived_at or datetime.max))


def process_yard_queue(db: Session) -> None:
    for s in yard_queue(db):
        try_dock_now(db, s)


def finish_unloading(db: Session) -> None:
    now = clock.now(db)
    for s in db.query(Shipment).filter(Shipment.status == "AT_DOCK").all():
        if s.docked_at and now - s.docked_at >= SLOT:
            appt = current_appointment(db, s)
            door = db.get(DockDoor, appt.door_id) if appt else None
            if appt:
                appt.status = "COMPLETED"
            if door:
                door.status = "AVAILABLE"
            s.unloaded_at = now
            if s.expected_qty is not None:
                s.received_qty = max(0, s.expected_qty - (s.shortfall_qty or 0))
            counted = (f"{s.received_qty} of {s.expected_qty} units counted"
                       if s.expected_qty is not None else "no manifest quantity (direct shipment)")
            transition(db, s, "shipment", "UNLOADED", module="e2", decided_by="rules:unload_timer",
                       reason=f"Unloaded at {door.door_no if door else '?'} after {DOCK_SLOT_MINUTES} min; {counted}.",
                       entity_id=s.shipment_ref,
                       payload={"po_ref": s.po_ref, "shipment_ref": s.shipment_ref,
                                "received_qty": s.received_qty, "expected_qty": s.expected_qty})
