"""Every CORRECT strategy, under the same 200-requests-for-one-seat race.

Required outcome for each: exactly 1 success, 199 clean failures (409, never
500), and exactly one booking row for the seat in the database.
"""
import asyncio

import pytest

from app.strategies import optimistic
from tests.concurrency import race_for_seat
from tests.conftest import EVENT_ID, HOT_SEAT_ID

CORRECT_STRATEGIES = ["pessimistic", "optimistic"]


@pytest.mark.parametrize("strategy", CORRECT_STRATEGIES)
async def test_200_requests_for_one_seat_give_exactly_one_booking(client, make_users, db, strategy):
    users = await make_users(200)

    statuses = await race_for_seat(client, users, HOT_SEAT_ID, strategy)

    print(f"\n{strategy}: {dict(statuses)}")
    assert statuses == {201: 1, 409: 199}
    assert await db.fetchval("SELECT count(*) FROM bookings WHERE seat_id = $1", HOT_SEAT_ID) == 1
    winner = await db.fetchval("SELECT user_id FROM bookings WHERE seat_id = $1", HOT_SEAT_ID)
    assert await db.fetchval("SELECT booked_by FROM seats WHERE id = $1", HOT_SEAT_ID) == winner
    assert await db.fetchval("SELECT count(*) FROM bookings GROUP BY seat_id HAVING count(*) > 1") is None


@pytest.mark.parametrize("strategy", CORRECT_STRATEGIES)
async def test_losers_get_a_clear_message(client, make_users, strategy):
    first, second = await make_users(2)
    body = {"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID], "strategy": strategy}
    await client.post("/book", json=body, headers=first)
    response = await client.post("/book", json=body, headers=second)
    assert response.status_code == 409
    assert "already booked" in response.json()["detail"]


@pytest.mark.parametrize("strategy", CORRECT_STRATEGIES)
async def test_overlapping_multi_seat_bookings_never_deadlock(client, make_users, db, strategy):
    """Half the users ask for seats [1, 2, 3], half for [3, 2, 1]. Without a
    global lock order this is the textbook deadlock."""
    users = await make_users(40)
    bodies = [
        {"event_id": EVENT_ID, "seat_ids": [1, 2, 3] if i % 2 else [3, 2, 1], "strategy": strategy}
        for i in range(len(users))
    ]
    responses = await asyncio.gather(
        *(client.post("/book", json=b, headers=h) for b, h in zip(bodies, users))
    )
    assert sorted(r.status_code for r in responses) == [201] + [409] * 39
    assert await db.fetchval("SELECT count(*) FROM bookings") == 3


async def test_different_seats_do_not_block_each_other(client, make_users, db):
    users = await make_users(50)
    responses = await asyncio.gather(*(
        client.post("/book", json={"event_id": EVENT_ID, "seat_ids": [i + 1], "strategy": "optimistic"},
                    headers=h)
        for i, h in enumerate(users)
    ))
    assert all(r.status_code == 201 for r in responses)
    assert all(r.json()["attempts"] == 1 for r in responses)  # no conflicts, no retries


async def test_optimistic_retries_after_a_version_conflict(client, make_users, db, monkeypatch):
    """Simulate another writer touching the seat (e.g. a price change) right
    between our read and our write, once. The first attempt must lose the
    compare-and-set, the second must succeed."""
    (user,) = await make_users(1)
    real_claim = optimistic.claim_seat
    interfered = {"times": 0}

    async def claim_after_interference(session, seat, user_id):
        if interfered["times"] == 0:
            interfered["times"] += 1
            await db.execute("UPDATE seats SET version = version + 1 WHERE id = $1", seat.id)
        return await real_claim(session, seat, user_id)

    monkeypatch.setattr(optimistic, "claim_seat", claim_after_interference)
    response = await client.post(
        "/book", json={"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID], "strategy": "optimistic"}, headers=user
    )
    assert response.status_code == 201
    assert response.json()["attempts"] == 2


async def test_optimistic_gives_up_after_max_retries(client_factory, make_users, db, monkeypatch):
    client = await client_factory(optimistic_max_retries=3)
    (user,) = await make_users(1)
    real_claim = optimistic.claim_seat
    calls = {"n": 0}

    async def always_interfere(session, seat, user_id):
        calls["n"] += 1
        await db.execute("UPDATE seats SET version = version + 1 WHERE id = $1", seat.id)
        return await real_claim(session, seat, user_id)

    monkeypatch.setattr(optimistic, "claim_seat", always_interfere)
    response = await client.post(
        "/book", json={"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID], "strategy": "optimistic"}, headers=user
    )
    assert response.status_code == 409
    assert "gave up after 4 attempts" in response.json()["detail"]
    assert calls["n"] == 4
    assert await db.fetchval("SELECT count(*) FROM bookings") == 0


async def test_pessimistic_times_out_instead_of_waiting_forever(client_factory, make_users, db):
    client = await client_factory(pessimistic_lock_timeout_ms=200)
    (user,) = await make_users(1)
    # Another transaction holds the row lock and never lets go (a stuck job).
    async with db.transaction():
        await db.execute("SELECT * FROM seats WHERE id = $1 FOR UPDATE", HOT_SEAT_ID)
        response = await client.post(
            "/book", json={"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID], "strategy": "pessimistic"},
            headers=user,
        )
    assert response.status_code == 409
    assert "busy" in response.json()["detail"]
