"""Seat holds: "this seat is reserved for you for 5 minutes".

A hold is a Redis key  hold:seat:<seat_id> = <user_id>  with a TTL.
- Expiry is automatic: when the TTL runs out Redis deletes the key and the
  seat shows as free again. No cleanup job, nothing to forget.
- Holding several seats is all-or-nothing and atomic (a Lua script), so two
  users selecting overlapping seats can never end up with half each.

Holds are a USER EXPERIENCE feature, not the correctness guarantee. The
booking strategies and the UNIQUE constraint still decide who gets a seat.
"""
from dataclasses import dataclass

from redis.asyncio import Redis

# Returns 0 on success, or the 1-based position of the first seat that is
# held by someone else. Re-holding your own seat refreshes its timer.
ACQUIRE_HOLDS = """
for i, key in ipairs(KEYS) do
    local holder = redis.call('GET', key)
    if holder and holder ~= ARGV[1] then
        return i
    end
end
for i, key in ipairs(KEYS) do
    redis.call('SET', key, ARGV[1], 'PX', ARGV[2])
end
return 0
"""

# Only the user who holds a seat can release it (same idea as the lock release).
RELEASE_HOLDS = """
local released = 0
for i, key in ipairs(KEYS) do
    if redis.call('GET', key) == ARGV[1] then
        released = released + redis.call('DEL', key)
    end
end
return released
"""


@dataclass
class Hold:
    user_id: int
    expires_in_ms: int


def hold_key(seat_id: int) -> str:
    return f"hold:seat:{seat_id}"


async def acquire(redis: Redis, user_id: int, seat_ids: list[int], ttl_seconds: float) -> int | None:
    """Hold all seats for `user_id`. Returns None on success, or the seat id
    that someone else is holding."""
    keys = [hold_key(s) for s in seat_ids]
    conflict = await redis.eval(ACQUIRE_HOLDS, len(keys), *keys, str(user_id), int(ttl_seconds * 1000))
    return None if conflict == 0 else seat_ids[conflict - 1]


async def release(redis: Redis, user_id: int, seat_ids: list[int]) -> int:
    if not seat_ids:
        return 0
    keys = [hold_key(s) for s in seat_ids]
    return await redis.eval(RELEASE_HOLDS, len(keys), *keys, str(user_id))


async def current(redis: Redis, seat_ids: list[int]) -> dict[int, Hold]:
    """Who holds which seat right now, and for how much longer."""
    async with redis.pipeline(transaction=False) as pipe:
        for seat_id in seat_ids:
            pipe.get(hold_key(seat_id))
            pipe.pttl(hold_key(seat_id))
        replies = await pipe.execute()
    holds = {}
    for index, seat_id in enumerate(seat_ids):
        holder, ttl_ms = replies[2 * index], replies[2 * index + 1]
        # ttl_ms is -2 if the key expired between the two commands.
        if holder is not None and ttl_ms > 0:
            holds[seat_id] = Hold(user_id=int(holder), expires_in_ms=ttl_ms)
    return holds


def held_by_others(holds: dict[int, Hold], user_id: int) -> list[int]:
    return [seat_id for seat_id, hold in holds.items() if hold.user_id != user_id]
