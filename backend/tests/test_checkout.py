"""Hold -> pay -> confirm, idempotency, and the late-payment edge cases."""
import asyncio
import uuid

import pytest

from tests.conftest import EVENT_ID, HOT_SEAT_ID


def key() -> dict:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def hold(client, headers, seat_ids):
    response = await client.post(f"/events/{EVENT_ID}/holds", json={"seat_ids": seat_ids}, headers=headers)
    assert response.status_code == 201, response.text
    return response


async def pay(client, headers, seat_ids, idem=None):
    return await client.post(
        "/payments", json={"event_id": EVENT_ID, "seat_ids": seat_ids}, headers={**headers, **(idem or key())}
    )


async def confirm(client, headers, payment_id, idem=None, strategy="pessimistic"):
    return await client.post(
        "/bookings/confirm", json={"payment_id": payment_id, "strategy": strategy},
        headers={**headers, **(idem or key())},
    )


@pytest.mark.parametrize("strategy", ["pessimistic", "optimistic", "redis"])
async def test_full_flow_hold_pay_confirm(client, make_users, db, strategy):
    (alice,) = await make_users(1)
    await hold(client, alice, [1, 2])
    payment = await pay(client, alice, [1, 2])
    assert payment.status_code == 201
    assert payment.json()["amount"] == "240.00"  # row A: 2 x 120.00

    booking = await confirm(client, alice, payment.json()["payment_id"], strategy=strategy)

    assert booking.status_code == 201, booking.text
    assert booking.json()["seat_ids"] == [1, 2]
    assert await db.fetchval("SELECT count(*) FROM bookings") == 2
    assert await db.fetchval("SELECT status FROM payments") == "confirmed"
    seats = (await client.get(f"/events/{EVENT_ID}/seats")).json()
    assert [s["state"] for s in seats[:2]] == ["booked", "booked"]
    mine = (await client.get("/bookings/me", headers=alice)).json()
    assert mine[0]["seats"] == ["A1", "A2"] and mine[0]["amount"] == "240.00"


async def test_endpoints_require_an_idempotency_key(client, make_users):
    (alice,) = await make_users(1)
    response = await client.post("/payments", json={"event_id": EVENT_ID, "seat_ids": [1]}, headers=alice)
    assert response.status_code == 400
    response = await client.post("/bookings/confirm", json={"payment_id": str(uuid.uuid4())}, headers=alice)
    assert response.status_code == 400


async def test_cannot_pay_for_seats_you_do_not_hold(client, make_users, db):
    (alice,) = await make_users(1)
    response = await pay(client, alice, [HOT_SEAT_ID])
    assert response.status_code == 409 and "not holding" in response.json()["detail"]
    assert await db.fetchval("SELECT count(*) FROM payments") == 0


async def test_retrying_a_payment_with_the_same_key_never_double_charges(client, make_users, db):
    (alice,) = await make_users(1)
    await hold(client, alice, [HOT_SEAT_ID])
    idem = key()

    first = await pay(client, alice, [HOT_SEAT_ID], idem)
    retry = await pay(client, alice, [HOT_SEAT_ID], idem)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert await db.fetchval("SELECT count(*) FROM payments") == 1


async def test_twenty_simultaneous_retries_charge_exactly_once(client, make_users, db):
    (alice,) = await make_users(1)
    await hold(client, alice, [HOT_SEAT_ID])
    idem = key()

    responses = await asyncio.gather(*(pay(client, alice, [HOT_SEAT_ID], idem) for _ in range(20)))

    assert await db.fetchval("SELECT count(*) FROM payments") == 1
    # The one that claimed the key gets 201. Duplicates either see the stored
    # 201 (if it already finished) or a 409 "still being processed".
    statuses = [r.status_code for r in responses]
    assert set(statuses) <= {201, 409} and 201 in statuses
    ids = {r.json()["payment_id"] for r in responses if r.status_code == 201}
    assert len(ids) == 1
    # A retry after things settle returns the original payment.
    assert (await pay(client, alice, [HOT_SEAT_ID], idem)).json()["payment_id"] in ids


async def test_same_key_with_a_different_body_is_rejected(client, make_users):
    (alice,) = await make_users(1)
    await hold(client, alice, [1, 2])
    idem = key()
    await pay(client, alice, [1], idem)
    response = await pay(client, alice, [1, 2], idem)
    assert response.status_code == 422


async def test_retrying_confirm_never_double_books(client, make_users, db):
    (alice,) = await make_users(1)
    await hold(client, alice, [HOT_SEAT_ID])
    payment_id = (await pay(client, alice, [HOT_SEAT_ID])).json()["payment_id"]
    idem = key()

    responses = [await confirm(client, alice, payment_id, idem) for _ in range(3)]

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["reference"] for r in responses}) == 1
    assert await db.fetchval("SELECT count(*) FROM bookings") == 1


async def test_same_payment_with_different_keys_books_once(client, make_users, db):
    """A buggy client that makes a NEW key per retry is still safe: the
    payment itself can only move charged -> confirming once."""
    (alice,) = await make_users(1)
    await hold(client, alice, [HOT_SEAT_ID])
    payment_id = (await pay(client, alice, [HOT_SEAT_ID])).json()["payment_id"]

    responses = await asyncio.gather(*(confirm(client, alice, payment_id) for _ in range(10)))

    assert sorted(r.status_code for r in responses) == [201] + [409] * 9
    assert await db.fetchval("SELECT count(*) FROM bookings") == 1


async def test_payment_just_after_hold_expired_still_books_if_seat_is_free(client_factory, make_users, db):
    client = await client_factory(hold_ttl_seconds=0.5)
    (alice,) = await make_users(1)
    await hold(client, alice, [HOT_SEAT_ID])
    payment_id = (await pay(client, alice, [HOT_SEAT_ID])).json()["payment_id"]

    await asyncio.sleep(0.7)  # the hold expires while "the card is being charged"

    response = await confirm(client, alice, payment_id)
    assert response.status_code == 201
    assert response.json()["late_hold_recovered"] is True
    assert await db.fetchval("SELECT status FROM payments") == "confirmed"


async def test_payment_after_hold_expired_and_seat_taken_is_refunded(client_factory, make_users, db):
    client = await client_factory(hold_ttl_seconds=0.5)
    alice, bob = await make_users(2)
    await hold(client, alice, [HOT_SEAT_ID])
    payment_id = (await pay(client, alice, [HOT_SEAT_ID])).json()["payment_id"]

    await asyncio.sleep(0.7)
    await hold(client, bob, [HOT_SEAT_ID])  # Bob grabs the freed seat

    response = await confirm(client, alice, payment_id)
    assert response.status_code == 409
    assert "refunded" in response.json()["detail"]
    assert await db.fetchval("SELECT status FROM payments") == "refunded"
    assert await db.fetchval("SELECT count(*) FROM bookings") == 0
    # Retrying the confirm replays the same answer; the refund is not undone.
    again = await confirm(client, alice, payment_id)
    assert again.status_code == 409 and "refunded" in again.json()["detail"]


async def test_seat_booked_directly_while_paying_triggers_refund(client, make_users, db, redis_client):
    alice, bob = await make_users(2)
    await hold(client, alice, [HOT_SEAT_ID])
    payment_id = (await pay(client, alice, [HOT_SEAT_ID])).json()["payment_id"]
    # Someone books the seat behind the hold's back (holds are advisory).
    await redis_client.delete(f"hold:seat:{HOT_SEAT_ID}")
    await client.post("/book", json={"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID]}, headers=bob)

    response = await confirm(client, alice, payment_id)

    assert response.status_code == 409 and "refunded" in response.json()["detail"]
    assert await db.fetchval("SELECT count(*) FROM bookings WHERE seat_id = $1", HOT_SEAT_ID) == 1
    assert await db.fetchval("SELECT status FROM payments") == "refunded"
