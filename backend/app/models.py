"""ORM models.

The important lines for concurrency are marked with "WHY". Everything else
is kept as small as the product allows.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SEAT_AVAILABLE = "available"
SEAT_BOOKED = "booked"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    venue: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("event_id", "row_label", "number", name="uq_seats_event_row_number"),
        CheckConstraint(f"status IN ('{SEAT_AVAILABLE}', '{SEAT_BOOKED}')", name="ck_seats_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    row_label: Mapped[str] = mapped_column(String(1))
    number: Mapped[int]
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # "held" is NOT stored here: holds live in Redis with a TTL so they expire
    # by themselves. The database only knows available vs booked.
    status: Mapped[str] = mapped_column(String(16), default=SEAT_AVAILABLE, server_default=SEAT_AVAILABLE)
    booked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # WHY: optimistic locking. Every write bumps it; a write that expected an
    # older version updates 0 rows and knows it lost the race.
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Booking(Base):
    """One row per booked seat. Seats bought together share a `reference`."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    # WHY unique: the last line of defence. Even if every lock in the
    # application had a bug, Postgres refuses a second booking for a seat.
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id"), unique=True)
    strategy: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
