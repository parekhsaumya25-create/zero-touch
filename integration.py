"""
THE joint between E2 and PR2 — and the only file allowed to know both.

E2 and PR2 never import each other. They publish domain events on the core
bus; this module subscribes and calls each side's public api.py:

  purchase_order.PO_CREATED  ->  E2 dispatches an inbound shipment for the PO
  shipment.IN_TRANSIT/IN_YARD/AT_DOCK  ->  PR2 mirrors the milestone on the PO
  shipment.UNLOADED          ->  PR2 records the goods receipt (source=dock)

Handlers only act in "full" mode. In "e2" or "pr2" mode each module runs
standalone: a PO is received manually, a shipment has no PO. Remove this file
and both modules still work — that's the decoupling guarantee.
"""

import logging

from sqlalchemy.orm import Session

from app.core import clock, events

log = logging.getLogger("zerotouch")

URGENCY_TO_PRIORITY = {"HIGH": "HIGH", "NORMAL": "MEDIUM", "LOW": "LOW"}


def _full(db: Session) -> bool:
    return clock.get_mode(db) == "full"


def on_po_released(db: Session, p: dict) -> None:
    if not _full(db):
        return
    from app.e2 import api as e2

    e2.dispatch_for_order(
        db, po_ref=p["po_no"], origin_city=p["supplier_city"], load_type=p["load_type"],
        priority=URGENCY_TO_PRIORITY.get(p.get("urgency", "NORMAL"), "MEDIUM"), expected_qty=p["quantity"],
        shortfall_qty=int(p.get("sim_shortfall_qty", 0)),
    )


def on_shipment_milestone(db: Session, p: dict) -> None:
    if not _full(db) or not p.get("po_ref"):
        return
    from app.pr2 import api as pr2

    pr2.update_logistics_status(
        db, p["po_ref"], p["to"], reason=f"{p['shipment_ref']}: {p['reason']}", source="event:e2.shipment",
    )


def on_shipment_unloaded(db: Session, p: dict) -> None:
    if not _full(db) or not p.get("po_ref") or p.get("received_qty") is None:
        return
    from app.pr2 import api as pr2

    pr2.receive_goods(db, p["po_ref"], int(p["received_qty"]), shipment_ref=p["shipment_ref"], source="dock")


def register() -> None:
    events.subscribe("purchase_order.PO_CREATED", on_po_released)
    for state in ("IN_TRANSIT", "IN_YARD", "AT_DOCK"):
        events.subscribe(f"shipment.{state}", on_shipment_milestone)
    events.subscribe("shipment.UNLOADED", on_shipment_unloaded)
    log.info("core integration handlers registered (active only in 'full' mode)")
