"""Optimistic locking: a version column, and retry on conflict.

"Optimistic" because we assume conflicts are RARE, so we do not lock at all.
We read the row and remember its version. When we write, we only update the
row if nobody changed it since:

    UPDATE seats SET status='booked', version = version + 1
    WHERE id = 42 AND version = 7          -- the version we read

If someone else wrote first, the version is now 8, our UPDATE matches 0 rows,
and we know we lost. Then we roll back, re-read and try again, up to
OPTIMISTIC_MAX_RETRIES times. If the re-read shows the seat is booked, we
stop immediately with "seat already booked"; retrying cannot help.

No transaction ever waits on another, which is why this is fast when
conflicts are rare, and wasteful when they are common (lots of retries).
"""
import asyncio
import random

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SEAT_AVAILABLE, SEAT_BOOKED, Booking, Seat
from app.strategies.common import (
    BookingRequest, BookingResult, SeatUnavailable, StrategyDeps, TooMuchContention, booking_rows,
    check_seats, select_seats,
)

NAME = "optimistic"


class VersionConflict(Exception):
    """Someone changed a seat between our read and our write."""


async def claim_seat(session: AsyncSession, seat: Seat, user_id: int) -> bool:
    """Compare-and-set on one seat. True if we won, False if the version moved."""
    result = await session.execute(
        update(Seat)
        .where(Seat.id == seat.id, Seat.version == seat.version, Seat.status == SEAT_AVAILABLE)
        .values(status=SEAT_BOOKED, booked_by=user_id, version=Seat.version + 1)
    )
    return result.rowcount == 1


async def try_once(deps: StrategyDeps, request: BookingRequest) -> None:
    async with deps.session_factory() as session, session.begin():
        seats = (await session.scalars(select_seats(request))).all()  # no FOR UPDATE: no lock
        check_seats(request, seats)  # seat really booked -> SeatUnavailable, no retry
        for seat in seats:
            if not await claim_seat(session, seat, request.user_id):
                raise VersionConflict()  # leaving the block rolls back seats we already claimed
        await session.execute(Booking.__table__.insert(), booking_rows(request, NAME))


async def book(deps: StrategyDeps, request: BookingRequest) -> BookingResult:
    max_attempts = deps.settings.optimistic_max_retries + 1
    for attempt in range(1, max_attempts + 1):
        try:
            await try_once(deps, request)
            return BookingResult(request.reference, request.seat_ids, NAME, attempts=attempt)
        except VersionConflict:
            # Random backoff so the losers do not all retry in lock-step and
            # collide again on the next attempt.
            await asyncio.sleep(random.uniform(0.002, 0.010) * attempt)
        except IntegrityError:
            raise SeatUnavailable("seat already booked")
    raise TooMuchContention(f"seat kept changing; gave up after {max_attempts} attempts")
