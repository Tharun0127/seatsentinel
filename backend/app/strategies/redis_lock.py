"""Redis distributed lock: SET key value NX PX, released with a Lua script.

Instead of asking Postgres to lock the row, every API worker (on any
machine) agrees to take a lock in Redis first:

    SET lock:seat:42 <random token> NX PX 5000

- NX: only set it if the key does not exist. Exactly one caller wins.
- PX 5000: the lock deletes itself after 5 s. If the worker holding it
  crashes, the seat is not blocked forever.
- token: a random value only the winner knows. Used for safe release.

Losers fail fast with 409 "seat is being booked by someone else" instead of
queuing. Whoever books after the winner has released the lock takes the lock,
reads Postgres, sees "booked", and gets "already booked".

Seats are locked in sorted id order, and a partial set of locks is released
at once, so two multi-seat requests can never wait on each other.
"""
import uuid

from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError

from app.strategies.common import (
    BookingRequest, BookingResult, SeatUnavailable, StrategyDeps, TooMuchContention, check_seats,
    select_seats, write_booking,
)

NAME = "redis"

# WHY A LUA SCRIPT TO RELEASE?
# The obvious release is `DEL lock:seat:42`. That is a bug:
#   1. Worker A takes the lock (PX 5000), then stalls for 6 s (GC pause, slow DB).
#   2. The lock expires by itself. Worker B takes it and starts booking.
#   3. Worker A wakes up, finishes and runs DEL, deleting B's lock.
#   4. Worker C now takes the lock while B is still inside its critical section.
# So release must be "delete only if the value is still MY token". Doing a
# GET and then a DEL from Python is check-then-act again: the lock could
# expire and change hands between the two calls. A Lua script runs atomically
# inside Redis; no other command can run between its GET and its DEL.
RELEASE_IF_OWNER = """
local released = 0
for i, key in ipairs(KEYS) do
    if redis.call('GET', key) == ARGV[1] then
        released = released + redis.call('DEL', key)
    end
end
return released
"""


def lock_key(seat_id: int) -> str:
    return f"lock:seat:{seat_id}"


async def acquire_all(redis: Redis, seat_ids: list[int], token: str, ttl_ms: int) -> list[str] | None:
    """Lock every seat or none of them. Returns the keys, or None if any is taken."""
    acquired: list[str] = []
    for seat_id in seat_ids:  # already sorted by BookingRequest
        key = lock_key(seat_id)
        if not await redis.set(key, token, nx=True, px=ttl_ms):
            await release(redis, acquired, token)
            return None
        acquired.append(key)
    return acquired


async def release(redis: Redis, keys: list[str], token: str) -> int:
    if not keys:
        return 0
    return await redis.eval(RELEASE_IF_OWNER, len(keys), *keys, token)


async def book(deps: StrategyDeps, request: BookingRequest) -> BookingResult:
    token = uuid.uuid4().hex
    keys = await acquire_all(deps.redis, request.seat_ids, token, deps.settings.redis_lock_ttl_ms)
    if keys is None:
        raise TooMuchContention("seat is being booked by someone else, please try again")
    try:
        # Inside the lock nobody else can be booking these seats, so a plain
        # SELECT (no FOR UPDATE) is enough.
        async with deps.session_factory() as session, session.begin():
            seats = (await session.scalars(select_seats(request))).all()
            check_seats(request, seats)
            await write_booking(session, request, NAME)
    except IntegrityError:
        # Only possible if the lock expired mid-transaction and someone else
        # got in. The unique constraint is the "fencing" safety net.
        raise SeatUnavailable("seat already booked")
    finally:
        await release(deps.redis, keys, token)
    return BookingResult(request.reference, request.seat_ids, NAME)
