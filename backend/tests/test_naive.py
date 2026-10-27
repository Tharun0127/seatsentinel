"""PROOF that the naive check-then-act booking is broken.

These tests PASS when the bug happens. They exist to demonstrate the race.
"""
import asyncio

from app.models import Booking
from app.strategies import naive
from app.strategies.common import BookingRequest, SeatUnavailable
from sqlalchemy.exc import IntegrityError
from tests.concurrency import race_for_seat
from tests.conftest import EVENT_ID, HOT_SEAT_ID, deps_for


async def test_naive_lets_many_users_book_the_same_seat(client, make_users, db):
    users = await make_users(200)

    statuses = await race_for_seat(client, users, HOT_SEAT_ID, "naive")

    winners = statuses[201]
    rows = await db.fetchval("SELECT count(*) FROM naive_bookings WHERE seat_id = $1", HOT_SEAT_ID)
    distinct_users = await db.fetchval(
        "SELECT count(DISTINCT user_id) FROM naive_bookings WHERE seat_id = $1", HOT_SEAT_ID
    )
    print(f"\nnaive: {winners} of 200 users were told they got seat A1; {rows} booking rows")
    assert winners > 1, "the race did not show up this time"
    assert rows == winners and distinct_users == winners
    # The seat row says only ONE of them owns it (last writer wins), so every
    # other "successful" customer has a ticket for a seat they do not own.
    owner = await db.fetchval("SELECT booked_by FROM seats WHERE id = $1", HOT_SEAT_ID)
    assert owner is not None


async def test_naive_works_fine_without_concurrency(client, make_users):
    first, second = await make_users(2)
    body = {"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID], "strategy": "naive"}
    assert (await client.post("/book", json=body, headers=first)).status_code == 201
    # One at a time, the check works. That is why this bug survives code
    # review and manual testing, and only shows up under real traffic.
    assert (await client.post("/book", json=body, headers=second)).status_code == 409


async def test_unique_constraint_catches_what_the_naive_logic_misses(client, make_users, db):
    """Same broken logic, but writing to the REAL bookings table.

    The application check still fails, yet Postgres's UNIQUE(seat_id)
    rejects every duplicate: defence in depth.
    """
    await make_users(50)
    deps = deps_for(client)
    requests = [BookingRequest(user_id=uid, event_id=EVENT_ID, seat_ids=[HOT_SEAT_ID]) for uid in range(1, 51)]

    outcomes = await asyncio.gather(
        *(naive.book(deps, r, ledger=Booking) for r in requests), return_exceptions=True
    )

    successes = [o for o in outcomes if not isinstance(o, Exception)]
    duplicates_blocked = [o for o in outcomes if isinstance(o, IntegrityError)]
    print(f"\nnaive vs real table: {len(successes)} booked, {len(duplicates_blocked)} blocked by UNIQUE")
    assert len(successes) == 1
    assert duplicates_blocked, "expected the race to reach the database at least once"
    assert all(isinstance(o, (IntegrityError, SeatUnavailable)) for o in outcomes if isinstance(o, Exception))
    assert await db.fetchval("SELECT count(*) FROM bookings WHERE seat_id = $1", HOT_SEAT_ID) == 1
