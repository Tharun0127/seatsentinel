"""Types and helpers shared by every booking strategy."""
import uuid
from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import SEAT_AVAILABLE, SEAT_BOOKED, Booking, Seat


@dataclass
class StrategyDeps:
    """What a strategy needs from the app. Passed in so tests can build it."""

    session_factory: async_sessionmaker
    redis: Redis
    settings: Settings


@dataclass
class BookingRequest:
    user_id: int
    event_id: int
    # Always sorted and de-duplicated. Locking seats in the same order in
    # every request is what prevents deadlocks when two people book
    # overlapping sets of seats (A1+A2 vs A2+A1).
    seat_ids: list[int]
    reference: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        self.seat_ids = sorted(set(self.seat_ids))


@dataclass
class BookingResult:
    reference: uuid.UUID
    seat_ids: list[int]
    strategy: str
    attempts: int = 1


class BookingError(Exception):
    """A clean, expected failure. The API turns it into a 409 or 404."""

    status_code = 409


class SeatUnavailable(BookingError):
    pass


class SeatNotFound(BookingError):
    status_code = 404


class TooMuchContention(BookingError):
    """Optimistic retries ran out, or a Redis lock is held by someone else."""


def check_seats(request: BookingRequest, seats: list[Seat]) -> None:
    """Raise if any requested seat is missing or already booked."""
    if len(seats) != len(request.seat_ids):
        raise SeatNotFound("one or more seats do not exist for this event")
    taken = [s.id for s in seats if s.status != SEAT_AVAILABLE]
    if taken:
        raise SeatUnavailable(f"seat(s) {taken} already booked")


def select_seats(request: BookingRequest):
    return (
        select(Seat)
        .where(Seat.event_id == request.event_id, Seat.id.in_(request.seat_ids))
        .order_by(Seat.id)
    )


async def write_booking(session: AsyncSession, request: BookingRequest, strategy: str) -> None:
    """Mark seats booked and insert one bookings row per seat (not committed)."""
    await session.execute(
        update(Seat)
        .where(Seat.id.in_(request.seat_ids))
        .values(status=SEAT_BOOKED, booked_by=request.user_id, version=Seat.version + 1)
    )
    await session.execute(insert(Booking), booking_rows(request, strategy))


def booking_rows(request: BookingRequest, strategy: str) -> list[dict]:
    return [
        {"reference": request.reference, "user_id": request.user_id, "event_id": request.event_id,
         "seat_id": seat_id, "strategy": strategy}
        for seat_id in request.seat_ids
    ]
