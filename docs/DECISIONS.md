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

## The naive (broken) endpoint

### Kept in the code, clearly marked, with its own ledger table
- `POST /book` with `strategy=naive` does check-then-act with no locking (`app/strategies/naive.py`).
- It writes to `naive_bookings`, a copy of `bookings` **without** the unique constraint. If it wrote to the real table, Postgres would reject the duplicates and the race would show up as errors instead of as many happy customers holding the same seat. The separate table makes the bug visible and countable.
- A second test runs the same naive code against the real `bookings` table and shows the unique constraint letting exactly one through. That is the "last line of defence" story in one test.

### Warm the connection pool before racing
- With a cold pool, most of the 200 requests spend their time opening connections and reach Postgres one after another, so the race barely shows (3 winners). After warming the pool, 20 of 200 users get the seat on every run (one per pooled connection), which is what a long-running server would see. Real traffic would be worse, not better.

## Correct strategies (shared rules)

- **One module per strategy** (`app/strategies/`), same signature `book(deps, request)`, selected by the `strategy` field of `POST /book`. Default is `pessimistic`.
- **Losers get 409 with a reason**, never a 500: "already booked", "seat is busy", or "gave up after N attempts". The concurrency tests assert `{201: 1, 409: 199}` exactly, so any 500 fails them.
- **Seat ids are sorted and de-duplicated** before any locking, so every request locks in the same global order and overlapping multi-seat bookings cannot deadlock. Tested with half the users asking for `[1,2,3]` and half for `[3,2,1]`.
- **Every strategy still catches `IntegrityError`** from the unique constraint and turns it into a clean 409.

## Pessimistic locking

- `SELECT ... FOR UPDATE` on the requested seats, then check, update and insert in the same transaction.
- **Why it is correct under READ COMMITTED:** when a waiting transaction finally gets the row lock, Postgres re-reads the latest committed version of that row before continuing, so it sees `booked`.
- **`SET LOCAL lock_timeout` (5 s):** without it, a stuck transaction holding a lock would make every waiter hang until the HTTP client gives up. With it, waiters get "seat is busy, please try again". Tested by holding a row lock from a second connection.
- **Alternative considered:** `FOR UPDATE NOWAIT` / `SKIP LOCKED`. NOWAIT fails instantly instead of queuing (good for "pick any seat" flows, harsher for users). SKIP LOCKED suits job queues, not "I want this specific seat".

## Optimistic locking

- Read without locks, then `UPDATE ... WHERE id = :id AND version = :read_version AND status = 'available'`. `rowcount == 0` means we lost.
- **Retry policy:** up to `OPTIMISTIC_MAX_RETRIES` (5) re-tries with a small random backoff that grows per attempt, so losers do not collide again in lock-step. If the re-read shows the seat is booked, we stop at once: retrying cannot help.
- **Honest note:** in this schema, a version change almost always means "someone booked it", so most conflicts end after one re-read with "already booked". Retries pay off when rows change for other reasons (a price edit, a cancellation). The tests simulate that by bumping the version between read and write.
- **Why `status = 'available'` is also in the WHERE:** belt and braces; even if versions were mishandled, an UPDATE can never flip a booked seat.

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
