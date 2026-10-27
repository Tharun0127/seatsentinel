"""Seed 3 events, each with rows A-J and 20 seats per row (200 seats).

Idempotent: ON CONFLICT DO NOTHING on the natural keys (event name, and
event + row + seat number), so running it again changes nothing and never
touches existing bookings.

Also creates one demo user (demo@seatsentinel.dev / demo1234) so the UI can
be tried without signing up.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import bcrypt

ROWS = "ABCDEFGHIJ"
SEATS_PER_ROW = 20
EVENTS = [
    ("Arctic Monkeys Live", "Wembley Arena", 14),
    ("Hamilton (Evening Show)", "Victoria Palace Theatre", 21),
    ("Champions League Final Screening", "Riverside Cinema Hall", 30),
]
DEMO_EMAIL = "demo@seatsentinel.dev"
DEMO_PASSWORD = "demo1234"


def price_for_row(row: str) -> Decimal:
    """Front rows cost more, like a real venue."""
    if row in "ABC":
        return Decimal("120.00")
    if row in "DEFG":
        return Decimal("80.00")
    return Decimal("50.00")


def seat_rows(event_id: int) -> list[tuple]:
    return [
        (event_id, row, number, price_for_row(row))
        for row in ROWS
        for number in range(1, SEATS_PER_ROW + 1)
    ]


async def seed(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        today = datetime.now(timezone.utc).replace(hour=19, minute=30, second=0, microsecond=0)
        for name, venue, days_ahead in EVENTS:
            await conn.execute(
                "INSERT INTO events (name, venue, starts_at) VALUES ($1, $2, $3) ON CONFLICT (name) DO NOTHING",
                name, venue, today + timedelta(days=days_ahead),
            )
            event_id = await conn.fetchval("SELECT id FROM events WHERE name = $1", name)
            await conn.executemany(
                "INSERT INTO seats (event_id, row_label, number, price) VALUES ($1, $2, $3, $4)"
                " ON CONFLICT ON CONSTRAINT uq_seats_event_row_number DO NOTHING",
                seat_rows(event_id),
            )
        demo_hash = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt(12)).decode()
        await conn.execute(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) ON CONFLICT (email) DO NOTHING",
            DEMO_EMAIL, demo_hash,
        )
        events = await conn.fetchval("SELECT count(*) FROM events")
        seats = await conn.fetchval("SELECT count(*) FROM seats")
        print(f"[seed] {events} events, {seats} seats, demo user {DEMO_EMAIL}")
    finally:
        await conn.close()


def main() -> None:
    url = os.getenv("DATABASE_URL", "postgresql+asyncpg://seats:seats@localhost:5432/seatsentinel")
    asyncio.run(seed(url.replace("postgresql+asyncpg://", "postgresql://")))


if __name__ == "__main__":
    main()
