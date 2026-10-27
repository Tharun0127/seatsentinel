"""Behaviour specific to the Redis distributed lock."""
import asyncio

from app.strategies import redis_lock
from tests.conftest import EVENT_ID, HOT_SEAT_ID


async def test_only_the_owner_can_release_the_lock(redis_client):
    keys = await redis_lock.acquire_all(redis_client, [HOT_SEAT_ID], "token-A", 5000)
    assert keys == ["lock:seat:1"]

    # Worker B (wrong token) tries to release A's lock: nothing happens.
    assert await redis_lock.release(redis_client, keys, "token-B") == 0
    assert await redis_client.get("lock:seat:1") == "token-A"

    assert await redis_lock.release(redis_client, keys, "token-A") == 1
    assert await redis_client.get("lock:seat:1") is None


async def test_lock_expires_by_itself_if_the_holder_dies(redis_client):
    await redis_lock.acquire_all(redis_client, [HOT_SEAT_ID], "crashed-worker", 100)
    assert await redis_lock.acquire_all(redis_client, [HOT_SEAT_ID], "me", 5000) is None
    await asyncio.sleep(0.15)
    assert await redis_lock.acquire_all(redis_client, [HOT_SEAT_ID], "me", 5000) == ["lock:seat:1"]


async def test_stale_holder_cannot_delete_the_new_owners_lock(redis_client):
    """The exact scenario the Lua script exists for."""
    old_keys = await redis_lock.acquire_all(redis_client, [HOT_SEAT_ID], "slow-worker", 100)
    await asyncio.sleep(0.15)  # slow worker stalls; its lock expires
    await redis_lock.acquire_all(redis_client, [HOT_SEAT_ID], "new-owner", 5000)

    await redis_lock.release(redis_client, old_keys, "slow-worker")  # wakes up, "releases"

    assert await redis_client.get("lock:seat:1") == "new-owner"


async def test_partial_multi_seat_lock_is_rolled_back(redis_client):
    await redis_client.set(redis_lock.lock_key(3), "someone-else", px=5000)
    assert await redis_lock.acquire_all(redis_client, [1, 2, 3], "me", 5000) is None
    # Seats 1 and 2 were locked on the way and must be free again.
    assert await redis_client.exists(redis_lock.lock_key(1), redis_lock.lock_key(2)) == 0


async def test_booking_while_locked_by_someone_else_is_a_clean_409(client, make_users, redis_client):
    (user,) = await make_users(1)
    await redis_client.set(redis_lock.lock_key(HOT_SEAT_ID), "another-worker", px=5000)
    response = await client.post(
        "/book", json={"event_id": EVENT_ID, "seat_ids": [HOT_SEAT_ID], "strategy": "redis"}, headers=user
    )
    assert response.status_code == 409
    assert "being booked by someone else" in response.json()["detail"]


async def test_lock_is_released_after_booking(client, make_users, redis_client):
    (user,) = await make_users(1)
    response = await client.post(
        "/book", json={"event_id": EVENT_ID, "seat_ids": [1, 2], "strategy": "redis"}, headers=user
    )
    assert response.status_code == 201
    assert await redis_client.exists(redis_lock.lock_key(1), redis_lock.lock_key(2)) == 0
