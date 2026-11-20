"""
Audit log + alerts. EVERY automated decision goes through audit() — that's the
"trustworthy autonomy" story: each action has who/what decided it, why, and
with what confidence.
"""

import logging

from sqlalchemy.orm import Session

from app.core import clock
from app.core.models import Alert, AuditLog

log = logging.getLogger("zerotouch")


def audit(
    db: Session,
    module: str,
    entity_type: str,
    entity_id,
    action: str,
    decided_by: str,
    reason: str,
    confidence: float | None = None,
) -> AuditLog:
    row = AuditLog(
        module=module,
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        decided_by=decided_by,
        reason=reason,
        confidence=None if confidence is None else round(float(confidence), 4),
        created_at=clock.now(db),
    )
    db.add(row)
    db.flush()
    log.info("[AUDIT] %s %s/%s %s by %s: %s", module, entity_type, entity_id, action, decided_by, reason)
    return row


def raise_alert(
    db: Session,
    module: str,
    entity_type: str,
    entity_id,
    code: str,
    severity: str,
    message: str,
) -> Alert:
    row = Alert(
        module=module,
        entity_type=entity_type,
        entity_id=str(entity_id),
        code=code,
        severity=severity,
        message=message,
        created_at=clock.now(db),
        resolved=False,
    )
    db.add(row)
    db.flush()
    log.warning("[ALERT %s] %s %s/%s %s", severity, code, entity_type, entity_id, message)
    return row


def resolve_alerts(db: Session, entity_type: str, entity_id, code: str | None = None) -> None:
    q = db.query(Alert).filter(Alert.entity_type == entity_type, Alert.entity_id == str(entity_id), Alert.resolved.is_(False))
    if code:
        q = q.filter(Alert.code == code)
    for a in q:
        a.resolved = True
    db.flush()
