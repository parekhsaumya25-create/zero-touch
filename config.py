"""
ALL business rules live here. Nothing in the services hardcodes a threshold —
if the panel asks "what if the approval limit changes?", it's one line here.
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------
# Swap to Postgres by changing this one line, e.g.
#   "postgresql+psycopg://user:pass@localhost:5432/zerotouch"
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'zerotouch.db'}")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")  # same model as the teammate prototype
# Used if the primary model is overloaded (503) or rate-limited (429) during a demo.
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.8-flash")
GEMINI_TIMEOUT_SECONDS = 10

DATA_DIR = ROOT / "data"
ML_MODELS_DIR = ROOT / "ml" / "models"
METRICS_PATH = ML_MODELS_DIR / "metrics.json"

RANDOM_SEED = 42

# Simulation clock: the demo runs on a simulated clock so a 300 km trip is
# watchable in minutes and every run is reproducible.
SIM_START = datetime(2026, 9, 28, 8, 0, 0)
TICK_SECONDS = 3                 # real seconds between scheduler ticks
SIM_MINUTES_PER_TICK = 5         # simulated minutes that pass per tick (100x)

# ---------------------------------------------------------------------------
# PR2 — procurement rules
# ---------------------------------------------------------------------------
AUTO_APPROVE_LIMIT = 50000       # INR; PO total at/above this needs a human
SUPPLIER_WEIGHTS = {"price": 0.4, "lead_time": 0.3, "otd": 0.2, "quality": 0.1}
MIN_OTD_RATE = 0.70
MAX_SUPPLIER_SHARE = 0.60        # max share of category spend (trailing window)
SUPPLIER_SHARE_WINDOW_DAYS = 90
PO_BUFFER_DAYS = 1               # expected_delivery = today + lead_time + buffer
PRICE_TOLERANCE_PCT = 0.02
QTY_TOLERANCE_PCT = 0.00
PARTIAL_RECEIPT_PCT = 0.05       # receipt short by more than this -> partial
PAYMENT_TERMS_DAYS = 30
LOW_CONFIDENCE_THRESHOLD = 0.80  # extraction confidence below -> human review

# ---------------------------------------------------------------------------
# E2 — yard & delivery rules
# ---------------------------------------------------------------------------
DELAY_REASSIGN_THRESHOLD = 0.70  # > : release dock slot, reassign, alert
DELAY_WARN_THRESHOLD = 0.40      # >= : warn ops, hold slot
DELAY_ETA_INFLATION = 0.30       # ETA stretched by p(delay) * 30% of remaining time
DOCK_SLOT_MINUTES = 60
DOCK_SEARCH_STEP_MINUTES = 15
DOCK_SEARCH_HORIZON_HOURS = 12
LATE_THRESHOLD_MINUTES = 30
DOCK_SCORE_WEIGHTS = {"window": 0.5, "zone": 0.3, "specialisation": 0.2}
LOAD_ZONE = {"DRY": "A", "REEFER": "B", "HAZMAT": "C", "OVERSIZE": "A"}
PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
PRIORITY_WAIT_PENALTY = {"HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.6}  # how much waiting hurts, by load priority
DISRUPTION_SPEED_KMPH = 12       # "simulate traffic jam" crawl speed
DISRUPTION_CONGESTION = 0.95
DISRUPTION_DURATION_MINUTES = 180

# ---------------------------------------------------------------------------
# Integrated-view KPI assumptions (used for "estimated savings")
# ---------------------------------------------------------------------------
MANUAL_COST_PER_TOUCH_INR = 350  # loaded cost of one manual clerical touch
MANUAL_TOUCHES_PER_ORDER = 6     # requisition, sourcing, PO, GRN, 3-way match, payment
