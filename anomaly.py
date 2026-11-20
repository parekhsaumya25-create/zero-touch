"""
Invoice anomaly detection (IsolationForest), layered ABOVE the match rules.

Features (per invoice, one-sided — only "more than usual" is suspicious):
price_vs_history (unit price above the SKU+supplier historical mean),
qty_vs_typical (log of qty over the typical order), log_total, supplier_risk
(1 - otd_rate). Trained by ml/train_anomaly.py. If the model file is
missing this returns (None, False, "") — i.e. the rules still run, the extra
layer is simply off — and logs that loudly.
"""

import logging
import math

import joblib
from sqlalchemy.orm import Session

from app.core.config import ML_MODELS_DIR
from app.pr2.models import Invoice, PurchaseOrder, Supplier

log = logging.getLogger("zerotouch")
MODEL_PATH = ML_MODELS_DIR / "anomaly_model.pkl"
FEATURES = ["price_vs_history", "qty_vs_typical", "log_total", "supplier_risk"]

_bundle = None
_warned = False


def _load():
    global _bundle
    if _bundle is None and MODEL_PATH.exists():
        _bundle = joblib.load(MODEL_PATH)
    return _bundle


def reload():
    global _bundle
    _bundle = None
    return _load()


def features(db: Session, inv: Invoice, po: PurchaseOrder | None) -> dict | None:
    if inv.unit_price is None or inv.quantity is None or not inv.sku:
        return None
    hist = (db.query(Invoice.unit_price, Invoice.quantity)
            .filter(Invoice.sku == inv.sku, Invoice.is_historical.is_(True), Invoice.id != inv.id))
    sku_rows = hist.all()
    sup_rows = hist.filter(Invoice.supplier_id == inv.supplier_id).all() if inv.supplier_id else []
    if not sku_rows:
        return None
    # Price is compared with what we've paid THIS supplier. Different suppliers
    # legitimately price differently (that's what sourcing scored), so with no
    # supplier history we fall back to the PO's agreed price, not other vendors'.
    if sup_rows:
        mean_price = sum(r[0] for r in sup_rows) / len(sup_rows)
    elif po is not None:
        mean_price = po.unit_price
    else:
        mean_price = sum(r[0] for r in sku_rows) / len(sku_rows)
    # Quantity is compared with the SKU's typical order across all suppliers.
    mean_qty = sum(r[1] for r in sku_rows) / len(sku_rows)
    sup = db.get(Supplier, inv.supplier_id) if inv.supplier_id else None
    return {
        "price_vs_history": max(0.0, inv.unit_price / mean_price - 1),
        "qty_vs_typical": math.log(max(1.0, inv.quantity / max(mean_qty, 1))),
        "log_total": math.log1p(max(inv.total, 0)),
        "supplier_risk": 1 - (sup.otd_rate if sup else 0.5),
    }


def score_invoice(db: Session, inv: Invoice, po: PurchaseOrder | None) -> tuple[float | None, bool, str]:
    """Returns (anomaly_score, flagged, explanation). Higher score = more anomalous."""
    global _warned
    bundle = _load()
    if bundle is None:
        if not _warned:
            log.warning("!!! ANOMALY MODEL NOT TRAINED — anomaly layer OFF. Run: python ml/train_anomaly.py !!!")
            _warned = True
        return None, False, ""
    f = features(db, inv, po)
    if f is None:
        return None, False, ""
    import pandas as pd

    x = pd.DataFrame([f])[FEATURES]
    score = float(-bundle["model"].score_samples(x)[0])
    flagged = score >= bundle["threshold"]
    why = (f"IsolationForest score {score:.3f} ≥ threshold {bundle['threshold']:.3f}: price "
           f"{f['price_vs_history']:+.1%} vs history, qty {math.exp(f['qty_vs_typical']):.1f}x typical, "
           f"supplier risk {f['supplier_risk']:.2f} — rules passed, flagged for review") if flagged else ""
    return round(score, 4), flagged, why
