"""
Explicit state machines. Every status change in the system goes through
transition(), which:
  1. rejects anything not in the transition table (no scattered if-statements),
  2. writes an AuditLog row (who/why/confidence),
  3. publishes "<machine>.<NEW_STATE>" on the core event bus — which is how
     each transition fires the next automated step.

The closed loop (tracked on the purchase order):
  REQUESTED -> PO_CREATED -> IN_TRANSIT -> IN_YARD -> AT_DOCK -> RECEIVED
            -> INVOICED -> MATCHED -> PAID
  EXCEPTION is reachable from every state.
  PO_CREATED -> RECEIVED directly is allowed: that's PR2 running standalone
  (manual goods receipt, no shipment/dock involved).
"""

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.events import publish

EXCEPTION = "EXCEPTION"

LOOP_STATES = [
    "REQUESTED", "PO_CREATED", "IN_TRANSIT", "IN_YARD", "AT_DOCK",
    "RECEIVED", "INVOICED", "MATCHED", "PAID",
]

MACHINES: dict[str, dict[str, set[str]]] = {
    # PR2 --------------------------------------------------------------
    "requisition": {
        "REQUESTED": {"PO_CREATED", EXCEPTION},
        EXCEPTION: {"REQUESTED", "CANCELLED"},
    },
    "purchase_order": {
        "PENDING_APPROVAL": {"PO_CREATED", "CANCELLED", EXCEPTION},
        "PO_CREATED": {"IN_TRANSIT", "RECEIVED", EXCEPTION},
        "IN_TRANSIT": {"IN_YARD", EXCEPTION},
        "IN_YARD": {"AT_DOCK", EXCEPTION},
        "AT_DOCK": {"RECEIVED", EXCEPTION},
        "RECEIVED": {"INVOICED", EXCEPTION},
        "INVOICED": {"MATCHED", EXCEPTION},
        "MATCHED": {"PAID", EXCEPTION},
        # A human resolving an exception can push the order back on track.
        EXCEPTION: {"PO_CREATED", "IN_TRANSIT", "IN_YARD", "AT_DOCK", "RECEIVED",
                    "INVOICED", "MATCHED", "PAID", "CANCELLED"},
    },
    "invoice": {
        "RECEIVED": {"AWAITING_RECEIPT", "MATCHED", EXCEPTION},
        "AWAITING_RECEIPT": {"MATCHED", EXCEPTION},
        "MATCHED": {"PAID", EXCEPTION},
        EXCEPTION: {"MATCHED", "PAID", "REJECTED"},
    },
    # E2 ---------------------------------------------------------------
    "shipment": {
        "SCHEDULED": {"IN_TRANSIT", EXCEPTION},
        "IN_TRANSIT": {"IN_YARD", EXCEPTION},
        "IN_YARD": {"AT_DOCK", EXCEPTION},
        "AT_DOCK": {"UNLOADED", EXCEPTION},
        EXCEPTION: {"IN_TRANSIT", "IN_YARD", "AT_DOCK", "UNLOADED"},
    },
}

TERMINAL = {"PAID", "CANCELLED", "REJECTED", "UNLOADED"}


class InvalidTransition(Exception):
    pass


def can_transition(machine: str, current: str, target: str) -> bool:
    if target == EXCEPTION and current not in TERMINAL:
        return True
    return target in MACHINES[machine].get(current, set())


def transition(
    db: Session,
    obj,
    machine: str,
    target: str,
    *,
    module: str,
    decided_by: str,
    reason: str,
    confidence: float | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
) -> None:
    current = obj.status
    if current == target:
        return
    if not can_transition(machine, current, target):
        raise InvalidTransition(f"{machine} {entity_id or obj.id}: {current} -> {target} is not allowed")
    obj.status = target
    db.flush()
    eid = entity_id or str(obj.id)
    audit(db, module, machine, eid, f"{current} -> {target}", decided_by, reason, confidence)
    event_payload = {"entity_id": eid, "from": current, "to": target, "reason": reason}
    event_payload.update(payload or {})
    publish(db, f"{machine}.{target}", event_payload)
