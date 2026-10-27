"""Seat holds: Redis keys with a TTL that expire on their own."""
import asyncio

from tests.conftest import EVENT_ID, HOT_SEAT_ID


def seat(seat_map: list[dict], seat_id: int) -> dict:
    return next(s for s in seat_map if s["id"] == seat_id)


async def hold(client, headers, seat_ids):
    return await client.post(f"/events/{EVENT_ID}/holds", json={"seat_ids": seat_ids}, headers=headers)


async def test_held_seat_shows_as_held_for_everyone_and_mine_for_me(client, make_users):
    alice, bob = await make_users(2)
    assert (await hold(client, alice, [HOT_SEAT_ID])).status_code == 201

    for_alice = seat((await client.get(f"/events/{EVENT_ID}/seats", headers=alice)).json(), HOT_SEAT_ID)
    for_bob = seat((await client.get(f"/events/{EVENT_ID}/seats", headers=bob)).json(), HOT_SEAT_ID)
    anonymous = seat((await client.get(f"/events/{EVENT_ID}/seats")).json(), HOT_SEAT_ID)

    assert for_alice["state"] == "held" and for_alice["mine"] is True
    assert 0 < for_alice["hold_expires_in_ms"] <= 300_000
    assert for_bob["state"] == "held" and for_bob["mine"] is False and for_bob["hold_expires_in_ms"] is None
    assert anonymous["state"] == "held"


async def test_second_user_cannot_hold_a_held_seat(client, make_users):
    alice, bob = await make_users(2)
    await hold(client, alice, [HOT_SEAT_ID])
    response = await hold(client, bob, [HOT_SEAT_ID, 2])
    assert response.status_code == 409
    assert "A1 was just taken by someone else" in response.json()["detail"]
    # All-or-nothing: Bob did not end up holding seat 2 either.
    assert seat((await client.get(f"/events/{EVENT_ID}/seats")).json(), 2)["state"] == "free"


async def test_only_one_of_many_concurrent_holds_wins(client, make_users):
    users = await make_users(100)
    responses = await asyncio.gather(*(hold(client, u, [HOT_SEAT_ID]) for u in users))
    assert sorted(r.status_code for r in responses) == [201] + [409] * 99


async def test_hold_expires_automatically(client_factory, make_users):
    client = await client_factory(hold_ttl_seconds=0.5)
    alice, bob = await make_users(2)
    await hold(client, alice, [HOT_SEAT_ID])
    assert (await hold(client, bob, [HOT_SEAT_ID])).status_code == 409

    await asyncio.sleep(0.7)

    assert seat((await client.get(f"/events/{EVENT_ID}/seats")).json(), HOT_SEAT_ID)["state"] == "free"
    assert (await hold(client, bob, [HOT_SEAT_ID])).status_code == 201


async def test_re_holding_your_own_seat_refreshes_the_timer(client, make_users, redis_client):
    (alice,) = await make_users(1)
    await hold(client, alice, [HOT_SEAT_ID])
    await redis_client.pexpire(f"hold:seat:{HOT_SEAT_ID}", 1000)
    assert (await hold(client, alice, [HOT_SEAT_ID])).status_code == 201
    assert await redis_client.pttl(f"hold:seat:{HOT_SEAT_ID}") > 290_000


async def test_cannot_hold_a_booked_seat(client, make_users):
    alice, bob = await make_users(2)
    await client.post("/book", json={"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID]}, headers=alice)
    response = await hold(client, bob, [HOT_SEAT_ID])
    assert response.status_code == 409 and "already booked" in response.json()["detail"]


async def test_direct_booking_respects_someone_elses_hold(client, make_users):
    alice, bob = await make_users(2)
    await hold(client, alice, [HOT_SEAT_ID])
    response = await client.post("/book", json={"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID]}, headers=bob)
    assert response.status_code == 409


async def test_user_can_release_their_hold_but_not_someone_elses(client, make_users):
    alice, bob = await make_users(2)
    await hold(client, alice, [HOT_SEAT_ID])
    released_by_bob = await client.request(
        "DELETE", f"/events/{EVENT_ID}/holds", json={"seat_ids": [HOT_SEAT_ID]}, headers=bob
    )
    assert released_by_bob.json()["released"] == 0
    released_by_alice = await client.request(
        "DELETE", f"/events/{EVENT_ID}/holds", json={"seat_ids": [HOT_SEAT_ID]}, headers=alice
    )
    assert released_by_alice.json()["released"] == 1
