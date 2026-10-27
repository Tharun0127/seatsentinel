"""Helper for the "200 people click the same seat at once" tests."""
import asyncio
from collections import Counter

from tests.conftest import EVENT_ID


async def warm_up_connection_pool(client, connections: int = 100) -> None:
    """Open DB connections before the race, like a server that has been running.

    With a cold pool, most of the 200 requests spend their time opening a
    connection and reach the database one after another, which hides races
    that a warm production server would hit.
    """
    await asyncio.gather(*(client.get("/events") for _ in range(connections)))


async def race_for_seat(client, users: list[dict], seat_id: int, strategy: str) -> Counter:
    """Every user tries to book `seat_id` at the same moment.

    asyncio.gather starts all requests before awaiting any, so they really
    are in flight together. Returns a Counter of HTTP status codes.
    """
    body = {"event_id": EVENT_ID, "seat_ids": [seat_id], "strategy": strategy}
    await warm_up_connection_pool(client)
    responses = await asyncio.gather(*(client.post("/book", json=body, headers=h) for h in users))
    return Counter(r.status_code for r in responses)
