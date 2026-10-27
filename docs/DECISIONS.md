# Decisions

Every important choice in SeatSentinel, the alternatives, and why.

## Data model

### Holds live in Redis, bookings live in Postgres
- **Chosen:** `seats.status` is only `available` or `booked`. A hold is a Redis key `hold:seat:<id>` with a 5-minute TTL.
- **Alternatives:** a `held` status plus `held_until` column in Postgres, cleaned by a background job.
- **Why:** holds are temporary by nature. A Redis TTL expires them exactly on time without a cleanup job, and a crashed client can never leave a seat stuck in "held". Bookings are permanent and money-related, so they belong in the transactional database.

### One `bookings` row per seat, grouped by `reference`
- **Why:** it lets a plain `UNIQUE (seat_id)` constraint guarantee "one booking per seat". A booking-with-many-seats table would need a separate join table to express the same rule.

### `UNIQUE (seat_id)` on bookings: the last line of defence
- Every locking strategy can have bugs. The unique constraint makes Postgres itself refuse a second booking for a seat, whatever the application does. Strategies catch the resulting `IntegrityError` and turn it into a clean 409.

### `version` column on seats
- Used only by the optimistic strategy. Kept on every seat so all strategies operate on the same schema and can be compared fairly.

## Auth

- **bcrypt (cost 12) run in a thread:** bcrypt is CPU-heavy on purpose; `asyncio.to_thread` keeps the event loop responsive. Tests use cost 4 and create users directly in SQL, because 200 signups at cost 12 would take about a minute.
- **JWT with only `sub` and `exp`:** the API is stateless; nothing else is needed.
- **Duplicate signup is caught by the unique index, not a SELECT-then-INSERT:** a check-then-act would itself be a race (the exact bug this project is about). A test fires 10 identical signups at once and expects exactly one user.

## Seed data

- 3 events, rows A-J, 20 seats per row. Front rows cost more (A-C 120, D-G 80, H-J 50).
- Idempotent via `ON CONFLICT DO NOTHING` on natural keys, so restarts never duplicate seats or touch bookings.
- A demo user (`demo@seatsentinel.dev` / `demo1234`) so the UI can be tried immediately.

## Testing

- Integration tests use real Postgres (`seatsentinel_test`) and real Redis (DB 15), reset before each test. Races, row locks, unique violations and `SET NX` only behave realistically in the real systems; fakes would hide the bugs.
- Concurrency tests fire requests through `httpx.AsyncClient` with `asyncio.gather` against an in-process app with a large connection pool (250), so 200 requests really are in flight at the same time.
