"""E2 master data (8 dock doors) + standalone demo shipments (no POs)."""

from sqlalchemy.orm import Session

from app.e2.models import DockDoor
from app.e2.services import tracking

DOORS = [
    # door, zone, compatible types. Only ONE reefer door on purpose: two cold
    # loads arriving together must produce a visible dock conflict.
    ("D1", "A", "DRY"),
    ("D2", "A", "DRY"),
    ("D3", "A", "DRY,OVERSIZE"),
    ("D4", "A", "DRY,OVERSIZE"),
    ("D5", "B", "REEFER"),
    ("D6", "B", "DRY"),
    ("D7", "C", "HAZMAT"),
    ("D8", "C", "DRY,HAZMAT"),
]

# route, load type, priority, starting progress (so trucks are mid-journey at demo start)
STANDALONE_SHIPMENTS = [
    ("R1", "DRY", "HIGH", 0.80),
    ("R4", "REEFER", "MEDIUM", 0.55),
    ("R3", "REEFER", "HIGH", 0.62),
    ("R2", "HAZMAT", "LOW", 0.30),
    ("R5", "OVERSIZE", "MEDIUM", 0.45),
]


def seed_doors(db: Session) -> None:
    for door_no, zone, types in DOORS:
        db.add(DockDoor(door_no=door_no, zone=zone, compatible_types=types, status="AVAILABLE"))
    db.flush()


def seed_standalone_shipments(db: Session) -> None:
    for route_id, load, prio, progress in STANDALONE_SHIPMENTS:
        tracking.create_shipment(db, route_id=route_id, load_type=load, priority=prio, start_progress=progress)
