"""
E2 tables: shipments, dock doors, dock appointments.

Deliberate decoupling: Shipment.po_ref is a plain nullable string, NOT a foreign
key into PR2's purchase_orders table. E2 works with po_ref = NULL (shipment
created directly), and never needs PR2's schema to exist.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_ref: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    trailer_id: Mapped[str] = mapped_column(String(20), index=True)
    tracking_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    po_ref: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    route_id: Mapped[str] = mapped_column(String(8))
    origin: Mapped[str] = mapped_column(String(60))
    destination: Mapped[str] = mapped_column(String(80))
    carrier: Mapped[str] = mapped_column(String(40))
    carrier_otd_rate: Mapped[float] = mapped_column(Float)
    load_type: Mapped[str] = mapped_column(String(10))       # DRY | REEFER | HAZMAT | OVERSIZE
    priority: Mapped[str] = mapped_column(String(8))         # HIGH | MEDIUM | LOW
    trailer_age_years: Mapped[float] = mapped_column(Float)
    congestion_score: Mapped[float] = mapped_column(Float)   # 0..1, route congestion
    expected_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shortfall_qty: Mapped[int] = mapped_column(Integer, default=0)  # simulation: units missing on unload
    distance_km: Mapped[float] = mapped_column(Float)
    travelled_km: Mapped[float] = mapped_column(Float, default=0.0)
    base_speed_kmph: Mapped[float] = mapped_column(Float)
    speed_kmph: Mapped[float] = mapped_column(Float)
    departed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_arrival: Mapped[datetime] = mapped_column(DateTime)
    current_lat: Mapped[float] = mapped_column(Float)
    current_lng: Mapped[float] = mapped_column(Float)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    eta: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delay_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    delay_band: Mapped[str] = mapped_column(String(8), default="OK")  # OK | WARN | HIGH
    disrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    disrupted_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="SCHEDULED")
    yard_arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    docked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class DockDoor(Base):
    __tablename__ = "dock_doors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    door_no: Mapped[str] = mapped_column(String(6), unique=True)
    compatible_types: Mapped[str] = mapped_column(String(60))  # CSV of load types
    zone: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(12), default="AVAILABLE")  # AVAILABLE | OCCUPIED | MAINTENANCE

    @property
    def types(self) -> list[str]:
        return [t.strip() for t in self.compatible_types.split(",") if t.strip()]


class DockAppointment(Base):
    __tablename__ = "dock_appointments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    door_id: Mapped[int] = mapped_column(ForeignKey("dock_doors.id"), index=True)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime)
    assigned_reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="BOOKED")  # BOOKED | ACTIVE | COMPLETED | RELEASED
    created_at: Mapped[datetime] = mapped_column(DateTime)
