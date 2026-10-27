"""!!! BROKEN ON PURPOSE: the naive booking, kept as an interview demo. !!!

It does "check, then act" with no locking:

    1. SELECT the seat. Is it available?      <- request A reads "available"
                                               <- request B reads "available"
    2. UPDATE it to booked, INSERT a booking.  <- A books it
                                               <- B books it too

Between step 1 and step 2 the code awaits the database, and other requests
run in that gap. They all see the seat as free, so they all "win". This is a
classic race condition (time-of-check to time-of-use, TOCTOU).

The endpoint records into `naive_bookings`, which has NO unique constraint,
so tests/test_naive.py can count the duplicate bookings it produces.
"""
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import SEAT_BOOKED, NaiveBooking, Seat
from app.strategies.common import BookingRequest, BookingResult, booking_rows, check_seats, select_seats

NAME = "naive"


async def book(session_factory: async_sessionmaker, request: BookingRequest, ledger=NaiveBooking) -> BookingResult:
    """`ledger` lets a test run this same broken logic against the real
    bookings table, to show the unique constraint catching what it misses."""
    async with session_factory() as session:
        # Step 1: CHECK. A plain SELECT takes no lock at all.
        seats = (await session.scalars(select_seats(request))).all()
        check_seats(request, seats)

        # Step 2: ACT. Nothing stops another request from having passed the
        # same check in the meantime. Note the UPDATE does not even re-check
        # the status in its WHERE clause.
        await session.execute(
            update(Seat).where(Seat.id.in_(request.seat_ids)).values(status=SEAT_BOOKED, booked_by=request.user_id)
        )
        await session.execute(insert(ledger), booking_rows(request, NAME))
        await session.commit()
    return BookingResult(request.reference, request.seat_ids, NAME)
