"""
Delay-risk prediction for a shipment -> probability of arriving late.

Serves predict_proba from the trained model (ml/models/delay_model.pkl) when
present. If the model file is missing it falls back to a transparent heuristic
and logs loudly that it is a STUB.
"""

import logging
import math
from datetime import datetime

import joblib

from app.core.config import ML_MODELS_DIR

log = logging.getLogger("zerotouch")

MODEL_PATH = ML_MODELS_DIR / "delay_model.pkl"
FEATURES = [
    "carrier_otd_rate", "distance_km", "planned_transit_hours", "departure_hour",
    "day_of_week", "is_monsoon", "route_congestion_score", "load_type", "trailer_age",
]
LOAD_TYPES = ["DRY", "REEFER", "HAZMAT", "OVERSIZE"]

_bundle = None
_warned = False


def _load():
    global _bundle
    if _bundle is None and MODEL_PATH.exists():
        _bundle = joblib.load(MODEL_PATH)
        log.info("delay model loaded: %s", _bundle.get("name"))
    return _bundle


def reload():
    global _bundle
    _bundle = None
    return _load()


def model_name() -> str:
    b = _load()
    return f"model:{b['name']}" if b else "STUB:heuristic_delay"


def build_features(shipment, departed_at: datetime | None) -> dict:
    dep = departed_at or shipment.created_at
    return {
        "carrier_otd_rate": shipment.carrier_otd_rate,
        "distance_km": shipment.distance_km,
        "planned_transit_hours": shipment.distance_km / max(shipment.base_speed_kmph, 1),
        "departure_hour": dep.hour,
        "day_of_week": dep.weekday(),
        "is_monsoon": int(dep.month in (6, 7, 8, 9)),
        "route_congestion_score": shipment.congestion_score,
        "load_type": shipment.load_type,
        "trailer_age": shipment.trailer_age_years,
    }


def _heuristic(f: dict) -> float:
    z = (-3.4 + 4.0 * (0.9 - f["carrier_otd_rate"]) + 3.0 * f["route_congestion_score"]
         + 0.6 * f["is_monsoon"] + 0.0015 * f["distance_km"] + 0.05 * f["trailer_age"])
    return 1 / (1 + math.exp(-z))


def predict(features: dict) -> float:
    global _warned
    bundle = _load()
    if bundle is None:
        if not _warned:
            log.warning("!!! DELAY MODEL NOT TRAINED — using STUB heuristic. Run: python ml/train_delay.py !!!")
            _warned = True
        return round(_heuristic(features), 4)
    import pandas as pd

    row = pd.DataFrame([features])[FEATURES]
    row["load_type"] = row["load_type"].map({t: i for i, t in enumerate(LOAD_TYPES)}).fillna(0).astype(int)
    return round(float(bundle["model"].predict_proba(row)[0, 1]), 4)
