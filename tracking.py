"""
Shipment lifecycle + live movement (E2).

Movement per tick (ported idea from Tanish's Truck-Live-Status, see geo.py):
  travelled += speed * dt  ->  locate that distance on the real road polyline.
ETA = remaining_km / speed, stretched by the delay model's probability.
Delay thresholds drive ACTIONS (config.py):
  p > DELAY_REASSIGN_THRESHOLD  -> release dock slot, re-book at new ETA, CRITICAL alert
  p >= DELAY_WARN_THRESHOLD     -> WARN alert to ops, hold the slot
  otherwise                     -> proceed
Traffic disruption (from the prototype's "simulate delay"): speed drops to a
crawl for a fixed window, congestion rises, risk is re-scored immediately.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit import audit, raise_alert, resolve_alerts
from app.core.config import (
    DELAY_ETA_INFLATION,
    DELAY_REASSIGN_THRESHOLD,
    DELAY_WARN_THRESHOLD,
    DISRUPTION_CONGESTION,
    DISRUPTION_DURATION_MINUTES,
    DISRUPTION_SPEED_KMPH,
    LATE_THRESHOLD_MINUTES,
)
from app.core.state_machine import transition
from app.e2.models import Shipment
from app.e2.services import delay, dock, geo

log = logging.getLogger("zerotouch")

# Carrier master data (E2-owned). OTD rate feeds the delay model.
CARRIERS = {
    "BlueDart Freight": 0.93,
    "Rivigo": 0.88,
    "VRL Logistics": 0.84,
    "Gati": 0.78,
    "TCI Freight": 0.71,
}
ROUTE_CARRIER = {"R1": "BlueDart Freight", "R2": "VRL Logistics", "R3": "Rivigo", "R4": "Gati", "R5": "TCI Freight",
                 "R6": "BlueDart Freight", "R7": "Rivigo"}
ROUTE_CONGESTION = {"R1": 0.35, "R2": 0.30, "R3": 0.25, "R4": 0.45, "R5": 0.55, "R6": 0.40, "R7": 0.60}
ROUTE_SPEED = {"R1": 55, "R2": 60, "R3": 55, "R4": 45, "R5": 50, "R6": 60, "R7": 30}


def _band(p: float) -> str:
    if p > DELAY_REASSIGN_THRESHOLD:
        return "HIGH"
    if p >= DELAY_WARN_THRESHOLD:
        return "WARN"
    return "OK"


def _remaining_hours(s: Shipment, now: datetime) -> float:
    remaining = max(0.0, s.distance_km - s.travelled_km)
    if s.disrupted and s.disrupted_until and s.disrupted_until > now:
        jam_h = (s.disrupted_until - now).total_seconds() / 3600
        jam_km = min(remaining, jam_h * DISRUPTION_SPEED_KMPH)
        return jam_km / DISRUPTION_SPEED_KMPH + (remaining - jam_km) / s.base_speed_kmph
    return remaining / max(s.speed_kmph, 1)


def _update_eta(s: Shipment, now: datetime) -> None:
    hours = _remaining_hours(s, now) * (1 + (s.delay_probability or 0) * DELAY_ETA_INFLATION)
    s.eta = now + timedelta(hours=hours)


def create_shipment(
    db: Session,
    *,
    route_id: str,
    load_type: str,
    priority: str = "MEDIUM",
    trailer_id: str | None = None,
    scheduled_arrival: datetime | None = None,
    po_ref: str | None = None,
    expected_qty: int | None = None,
    carrier: str | None = None,
    start_progress: float = 0.0,
    shortfall_qty: int = 0,
    congestion: float | None = None,
) -> Shipment:
    """Create + dispatch a shipment. Works with po_ref=None (E2 standalone)."""
    route = geo.get_route(route_id)
    now = clock.now(db)
    seq = clock.next_seq(db, "shipment")
    carrier = carrier or ROUTE_CARRIER[route_id]
    base_speed = ROUTE_SPEED[route_id]
    length = route["length_km"]
    travelled = length * max(0.0, min(start_progress, 0.99))
    departed = now - timedelta(hours=travelled / base_speed)
    planned_arrival = departed + timedelta(hours=length / base_speed)
    lat, lng, _ = geo.position_at_distance(route["coords"], route["cum"], travelled)

    s = Shipment(
        shipment_ref=f"SHP-{1000 + seq}",
        tracking_no=f"ZT{7310000 + seq * 37}",
        trailer_id=trailer_id or f"TRL-{500 + seq}",
        po_ref=po_ref,
        route_id=route_id,
        origin=route["origin"],
        destination=route["destination"],
        carrier=carrier,
        carrier_otd_rate=CARRIERS.get(carrier, 0.8),
        load_type=load_type,
        priority=priority,
        trailer_age_years=float(2 + (seq * 3) % 9),
        congestion_score=ROUTE_CONGESTION[route_id] if congestion is None else congestion,
        expected_qty=expected_qty,
        shortfall_qty=shortfall_qty,
        distance_km=round(length, 1),
        travelled_km=travelled,
        base_speed_kmph=base_speed,
        speed_kmph=base_speed,
        departed_at=departed,
        scheduled_arrival=scheduled_arrival or planned_arrival,
        current_lat=lat,
        current_lng=lng,
        progress_pct=round(100 * travelled / length, 1),
        status="SCHEDULED",
        created_at=now,
    )
    db.add(s)
    db.flush()

    s.delay_probability = delay.predict(delay.build_features(s, departed))
    s.delay_band = _band(s.delay_probability)
    _update_eta(s, now)
    link = f"for {po_ref}" if po_ref else "created directly (no linked PO)"
    transition(
        db, s, "shipment", "IN_TRANSIT", module="e2", decided_by=delay.model_name(),
        reason=(f"{s.shipment_ref} {link}: {route['origin']} -> DC, {s.distance_km:.0f} km via {carrier}. "
                f"Delay risk {s.delay_probability:.0%} ({s.delay_band}); ETA {s.eta:%d %b %H:%M}."),
        confidence=s.delay_probability, entity_id=s.shipment_ref,
        payload={"po_ref": po_ref, "shipment_ref": s.shipment_ref, "tracking_no": s.tracking_no},
    )
    if s.delay_band != "OK":
        _raise_risk_alert(db, s)
    dock.book(db, s, s.eta)
    return s


def set_progress(db: Session, s: Shipment, progress: float) -> None:
    """Seeding helper: place an in-transit truck part-way along its route and re-book its slot."""
    now = clock.now(db)
    route = geo.get_route(s.route_id)
    s.travelled_km = s.distance_km * max(0.0, min(progress, 0.99))
    s.departed_at = now - timedelta(hours=s.travelled_km / s.base_speed_kmph)
    s.scheduled_arrival = s.departed_at + timedelta(hours=s.distance_km / s.base_speed_kmph)
    s.current_lat, s.current_lng, _ = geo.position_at_distance(route["coords"], route["cum"], s.travelled_km)
    s.progress_pct = round(100 * s.travelled_km / s.distance_km, 1)
    _update_eta(s, now)
    appt = dock.current_appointment(db, s)
    if appt and appt.status == "BOOKED":
        db.delete(appt)  # seeding only: replace the dispatch-time booking silently
        db.flush()
    dock.book(db, s, s.eta)


def _raise_risk_alert(db: Session, s: Shipment) -> None:
    sev = "CRITICAL" if s.delay_band == "HIGH" else "WARN"
    raise_alert(db, "e2", "shipment", s.shipment_ref, f"DELAY_RISK_{s.delay_band}", sev,
                f"{s.shipment_ref} delay risk {s.delay_probability:.0%} (ETA {s.eta:%H:%M}).")


def evaluate_risk(db: Session, s: Shipment) -> None:
    """Re-score delay risk and act on threshold crossings (the model DRIVES the decision)."""
    now = clock.now(db)
    s.delay_probability = delay.predict(delay.build_features(s, s.departed_at))
    _update_eta(s, now)
    new_band = _band(s.delay_probability)
    appt = dock.current_appointment(db, s)
    decided_by = delay.model_name()

    if new_band != s.delay_band:
        old = s.delay_band
        s.delay_band = new_band
        if new_band == "HIGH":
            _raise_risk_alert(db, s)
            reason = (f"delay risk {s.delay_probability:.0%} > {DELAY_REASSIGN_THRESHOLD:.0%}; "
                      f"new ETA {s.eta:%H:%M}")
            if appt and appt.status == "BOOKED":
                dock.release(db, appt, reason)
            audit(db, "e2", "shipment", s.shipment_ref, "RISK_HIGH_REASSIGN", decided_by,
                  f"Risk {old} -> HIGH: {reason}. Slot released and re-booked.", s.delay_probability)
            dock.book(db, s, s.eta, context="REASSIGNED after delay-risk spike: ")
            return
        if new_band == "WARN":
            _raise_risk_alert(db, s)
            audit(db, "e2", "shipment", s.shipment_ref, "RISK_WARN_HOLD", decided_by,
                  f"Risk {old} -> WARN ({s.delay_probability:.0%}); ops warned, dock slot held.", s.delay_probability)
        else:
            resolve_alerts(db, "shipment", s.shipment_ref)
            audit(db, "e2", "shipment", s.shipment_ref, "RISK_CLEARED", decided_by,
                  f"Risk {old} -> OK ({s.delay_probability:.0%}); proceeding.", s.delay_probability)

    # Independent of the model: if the ETA has drifted past the booked slot, the
    # slot will be missed anyway — free it for someone else and re-book.
    if appt and appt.status == "BOOKED" and s.eta > appt.scheduled_end + timedelta(minutes=LATE_THRESHOLD_MINUTES):
        dock.release(db, appt, f"ETA {s.eta:%H:%M} is past the slot end {appt.scheduled_end:%H:%M}")
        dock.book(db, s, s.eta, context="REASSIGNED (ETA drifted past slot): ")


def move(db: Session, s: Shipment, minutes: float) -> None:
    now = clock.now(db)
    route = geo.get_route(s.route_id)
    if s.disrupted and s.disrupted_until and now >= s.disrupted_until:
        clear_disruption(db, s, auto=True)
    s.travelled_km = min(s.distance_km, s.travelled_km + s.speed_kmph * minutes / 60)
    lat, lng, finished = geo.position_at_distance(route["coords"], route["cum"], s.travelled_km)
    s.current_lat, s.current_lng = lat, lng
    s.progress_pct = round(100 * s.travelled_km / s.distance_km, 1)
    _update_eta(s, now)
    if finished or s.progress_pct >= 100:
        arrive(db, s)


def arrive(db: Session, s: Shipment) -> None:
    now = clock.now(db)
    s.progress_pct, s.travelled_km, s.eta, s.yard_arrived_at = 100.0, s.distance_km, now, now
    late_min = (now - s.scheduled_arrival).total_seconds() / 60
    timing = (f"{late_min:.0f} min LATE vs scheduled {s.scheduled_arrival:%H:%M}" if late_min > LATE_THRESHOLD_MINUTES
              else f"on time (scheduled {s.scheduled_arrival:%H:%M})")
    transition(db, s, "shipment", "IN_YARD", module="e2", decided_by="gps:geofence",
               reason=f"Entered DC geofence at {now:%H:%M}, {timing}.", entity_id=s.shipment_ref,
               payload={"po_ref": s.po_ref, "shipment_ref": s.shipment_ref})
    for code in ("DELAY_RISK_HIGH", "DELAY_RISK_WARN"):  # the risk has played out; the truck is here
        resolve_alerts(db, "shipment", s.shipment_ref, code)
    if late_min > LATE_THRESHOLD_MINUTES:
        raise_alert(db, "e2", "shipment", s.shipment_ref, "LATE_ARRIVAL", "WARN",
                    f"{s.shipment_ref} arrived {late_min:.0f} min late.")
    if not dock.try_dock_now(db, s):
        audit(db, "e2", "shipment", s.shipment_ref, "HELD_IN_YARD", "rules:dock_greedy",
              f"No compatible {s.load_type} door free without conflict; queued (priority {s.priority}, arrived {now:%H:%M}).")


def disrupt(db: Session, s: Shipment) -> Shipment:
    if s.status != "IN_TRANSIT":
        raise ValueError(f"{s.shipment_ref} is {s.status}, only IN_TRANSIT trucks can be disrupted")
    now = clock.now(db)
    s.disrupted = True
    s.disrupted_until = now + timedelta(minutes=DISRUPTION_DURATION_MINUTES)
    s.speed_kmph = DISRUPTION_SPEED_KMPH
    s.congestion_score = DISRUPTION_CONGESTION
    audit(db, "e2", "shipment", s.shipment_ref, "DISRUPTION", "event:traffic_feed",
          f"Heavy traffic on {s.origin} route: speed {s.base_speed_kmph:.0f} -> {DISRUPTION_SPEED_KMPH} km/h "
          f"until {s.disrupted_until:%H:%M}; congestion {DISRUPTION_CONGESTION}.")
    evaluate_risk(db, s)
    return s


def clear_disruption(db: Session, s: Shipment, auto: bool = False) -> None:
    s.disrupted = False
    s.disrupted_until = None
    s.speed_kmph = s.base_speed_kmph
    s.congestion_score = ROUTE_CONGESTION.get(s.route_id, 0.3)
    audit(db, "e2", "shipment", s.shipment_ref, "DISRUPTION_CLEARED", "event:traffic_feed",
          f"Traffic cleared{' (window elapsed)' if auto else ''}; speed back to {s.base_speed_kmph:.0f} km/h.")
    evaluate_risk(db, s)


def tick(db: Session, minutes: float) -> None:
    """Registered with the core scheduler. Order matters: free doors first, then fill them."""
    dock.finish_unloading(db)
    dock.process_yard_queue(db)
    for s in db.query(Shipment).filter(Shipment.status == "IN_TRANSIT").order_by(Shipment.id).all():
        move(db, s, minutes)


def search(db: Session, q: str) -> list[Shipment]:
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    return (
        db.query(Shipment)
        .filter(or_(Shipment.tracking_no.ilike(like), Shipment.trailer_id.ilike(like),
                    Shipment.shipment_ref.ilike(like), Shipment.po_ref.ilike(like)))
        .order_by(Shipment.id.desc())
        .limit(20)
        .all()
    )
