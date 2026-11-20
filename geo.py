"""
Route geometry for truck movement.

PORTED from the teammate prototype Tanish1431/Truck-Live-Status (server.js,
author: Tanish Khandelwal). Original JavaScript -> Python, logic preserved:
  - haversineDistance()          -> haversine_km()
  - buildCumulativeDistances()   -> cumulative_km()
  - getPositionAtDistance()      -> position_at_distance()  (binary search + interpolation)
Key idea kept from the original: movement is SPEED-based (distance += speed * dt,
then locate that distance on the road polyline), not "jump to next waypoint".
Changes: km instead of metres; trucks stop at the destination (the original
reversed back and forth forever) so arrival can hand off to the yard.
"""

import bisect
import json
import math
from functools import lru_cache
from pathlib import Path

ROUTES_FILE = Path(__file__).resolve().parents[1] / "data" / "routes.json"
EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance: a = sin²(Δφ/2) + cos φ1 cos φ2 sin²(Δλ/2); d = 2R atan2(√a, √(1−a))."""
    to_rad = math.radians
    d_lat = to_rad(lat2 - lat1)
    d_lon = to_rad(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(to_rad(lat1)) * math.cos(to_rad(lat2)) * math.sin(d_lon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def cumulative_km(coords: list[list[float]]) -> list[float]:
    cum = [0.0]
    for i in range(1, len(coords)):
        cum.append(cum[-1] + haversine_km(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]))
    return cum


def position_at_distance(coords, cum, distance_km: float) -> tuple[float, float, bool]:
    """(lat, lng, finished) at `distance_km` along the polyline."""
    total = cum[-1]
    if distance_km <= 0:
        return coords[0][0], coords[0][1], False
    if distance_km >= total:
        return coords[-1][0], coords[-1][1], True
    hi = bisect.bisect_right(cum, distance_km)
    lo = hi - 1
    seg = cum[hi] - cum[lo]
    t = (distance_km - cum[lo]) / seg if seg > 0 else 0.0
    lat = coords[lo][0] + (coords[hi][0] - coords[lo][0]) * t
    lng = coords[lo][1] + (coords[hi][1] - coords[lo][1]) * t
    return round(lat, 6), round(lng, 6), False


@lru_cache(maxsize=1)
def load_routes() -> dict:
    """Routes baked from OSRM by tools/fetch_routes.py (real road geometry, offline at runtime)."""
    data = json.loads(ROUTES_FILE.read_text())
    routes = {}
    for r in data["routes"]:
        cum = cumulative_km(r["coords"])
        routes[r["route_id"]] = {**r, "cum": cum, "length_km": cum[-1]}
    return {"dc": data["dc"], "routes": routes}


def get_route(route_id: str) -> dict:
    routes = load_routes()["routes"]
    if route_id not in routes:
        raise KeyError(f"unknown route {route_id}; valid: {sorted(routes)}")
    return routes[route_id]


def route_for_origin(city: str) -> str | None:
    for rid, r in load_routes()["routes"].items():
        if r["origin"].lower() == (city or "").lower():
            return rid
    return None
