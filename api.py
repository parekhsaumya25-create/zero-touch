"""
E2 public interface. The ONLY E2 functions the integration layer
(app/core/integration.py) is allowed to call. Plain types in, plain types out.
"""

from sqlalchemy.orm import Session

from app.e2.services import geo, tracking


def dispatch_for_order(
    db: Session,
    *,
    po_ref: str,
    origin_city: str,
    load_type: str,
    priority: str,
    expected_qty: int,
    shortfall_qty: int = 0,
) -> str:
    """Create an inbound shipment for an external order reference. Returns shipment_ref."""
    route_id = geo.route_for_origin(origin_city) or "R1"
    s = tracking.create_shipment(
        db, route_id=route_id, load_type=load_type, priority=priority,
        po_ref=po_ref, expected_qty=expected_qty, shortfall_qty=shortfall_qty,
    )
    return s.shipment_ref


def tick(db: Session, minutes: float) -> None:
    tracking.tick(db, minutes)
