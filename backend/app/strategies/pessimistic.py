"""Pessimistic locking: SELECT ... FOR UPDATE inside a transaction.

"Pessimistic" because we assume a conflict WILL happen, so we lock first and
check second:

    BEGIN;
    SELECT * FROM seats WHERE id IN (...) ORDER BY id FOR UPDATE;  -- row locks
    -- any other transaction that wants these rows now WAITS here
    check they are available, UPDATE them, INSERT bookings;
    COMMIT;                                                        -- locks released

The 199 losers queue up on the row lock. When the winner commits, each one
gets the lock in turn, re-reads the row (Postgres re-checks a locked row
after waiting), sees "booked", and fails cleanly.

ORDER BY id matters: two requests locking {A1, A2} and {A2, A1} in different
orders could each grab one row and wait for the other forever (deadlock).
Locking in one global order makes that impossible.
"""
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.strategies.common import (
    BookingRequest, BookingResult, SeatUnavailable, StrategyDeps, TooMuchContention, check_seats,
    select_seats, write_booking,
)

NAME = "pessimistic"
LOCK_NOT_AVAILABLE = "55P03"  # Postgres error code when lock_timeout fires


async def book(deps: StrategyDeps, request: BookingRequest) -> BookingResult:
    try:
        async with deps.session_factory() as session, session.begin():
            # Without a timeout, a request could wait on a lock for as long as
            # the slowest transaction takes. SET LOCAL lasts until COMMIT.
            timeout_ms = int(deps.settings.pessimistic_lock_timeout_ms)
            await session.execute(text(f"SET LOCAL lock_timeout = '{timeout_ms}ms'"))

            seats = (await session.scalars(select_seats(request).with_for_update())).all()
            check_seats(request, seats)
            await write_booking(session, request, NAME)
    except IntegrityError:
        # Should never happen while the lock works; if it does, the unique
        # constraint saved us and the user still gets a clean answer.
        raise SeatUnavailable("seat already booked")
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE:
            raise TooMuchContention("seat is busy, please try again")
        raise
    return BookingResult(request.reference, request.seat_ids, NAME)
