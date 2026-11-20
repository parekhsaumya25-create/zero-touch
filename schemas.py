from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.schemas import ORM

LoadType = Literal["DRY", "REEFER", "HAZMAT", "OVERSIZE"]
Priority = Literal["HIGH", "MEDIUM", "LOW"]


class ShipmentCreate(BaseModel):
    trailer_id: str | None = None
    route_id: Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
    load_type: LoadType = "DRY"
    priority: Priority = "MEDIUM"
    scheduled_arrival: datetime | None = None
    po_ref: str | None = None
    expected_qty: int | None = Field(default=None, ge=0)
    start_progress: float = Field(default=0.0, ge=0, le=0.99)


class AppointmentOut(ORM):
    id: int
    door_no: str | None = None
    scheduled_start: datetime
    scheduled_end: datetime
    assigned_reason: str
    status: str


class ShipmentOut(ORM):
    id: int
    shipment_ref: str
    trailer_id: str
    tracking_no: str
    po_ref: str | None
    po_note: str
    route_id: str
    origin: str
    destination: str
    carrier: str
    load_type: str
    priority: str
    expected_qty: int | None
    received_qty: int | None
    distance_km: float
    travelled_km: float
    speed_kmph: float
    current_lat: float
    current_lng: float
    progress_pct: float
    departed_at: datetime | None
    scheduled_arrival: datetime
    eta: datetime | None
    delay_probability: float | None
    delay_band: str
    disrupted: bool
    status: str
    yard_arrived_at: datetime | None
    docked_at: datetime | None
    unloaded_at: datetime | None
    appointment: AppointmentOut | None = None


class DoorOut(BaseModel):
    door_no: str
    zone: str
    compatible_types: list[str]
    status: str
    current: dict | None
    upcoming: list[dict]
