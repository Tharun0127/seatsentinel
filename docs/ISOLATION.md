# Transaction isolation: READ COMMITTED vs SERIALIZABLE

Two people, Alice and Bob, try to book seat 1 at the same time. Both
transactions read the seat first, then write. The demo interleaves the steps
by hand so the race happens on every run:

```
Alice: BEGIN; SELECT status  -> 'available'
Bob:   BEGIN; SELECT status  -> 'available'
Alice: UPDATE seat -> booked by alice; COMMIT
Bob:   UPDATE seat -> booked by bob;   COMMIT ?
```

Run it yourself: `docker compose exec api python -m scripts.isolation_demo`
(code: [`backend/scripts/isolation_demo.py`](../backend/scripts/isolation_demo.py),
asserted by `tests/test_isolation.py`).

## Output

```
1. READ COMMITTED, blind UPDATE
   Alice reads 'available', Bob reads 'available'
   Alice books it and commits
   Bob's UPDATE: UPDATE 1 -> commit OK
   Final row: {'status': 'booked', 'booked_by': 'bob'}
   => DOUBLE BOOKED (lost update)

2. SERIALIZABLE, blind UPDATE
   Alice reads 'available', Bob reads 'available'
   Alice books it and commits
   Bob's UPDATE fails: 40001 could not serialize access due to concurrent update
   Final row: {'status': 'booked', 'booked_by': 'alice'}
   => correct: only Alice has it

3. READ COMMITTED, conditional UPDATE
   Alice reads 'available', Bob reads 'available'
   Alice books it and commits
   Bob's UPDATE: UPDATE 0 -> commit OK
   Final row: {'status': 'booked', 'booked_by': 'alice'}
   => correct: only Alice has it
```

## What each level means for this use case

| | READ COMMITTED (Postgres default) | SERIALIZABLE |
|---|---|---|
| What a statement sees | Everything committed before **that statement** started | A snapshot from the start of the **transaction** |
| Read-then-write race | Allowed: Bob's blind UPDATE overwrites Alice (lost update) | Detected: Bob gets error `40001` and must retry |
| Cost | Cheapest, never aborts for serialization reasons | Extra tracking (predicate locks); transactions can abort under contention, so **every** caller needs a retry loop |

## Why SeatSentinel stays on READ COMMITTED

Raising the isolation level is one valid fix, but it moves the problem: every
endpoint must now catch `40001` and retry, and under a hot on-sale many
transactions abort and retry. Instead each strategy makes the race impossible
at READ COMMITTED:

- **Pessimistic:** `SELECT ... FOR UPDATE` makes Bob wait for Alice's lock.
  When he gets it, Postgres re-reads the row and he sees `booked`.
- **Optimistic:** the conditional UPDATE in scenario 3 (`WHERE version = :v`),
  with `rowcount == 0` meaning "you lost".
- **Redis lock:** Bob never reaches the database while Alice holds the lock.
- **And always:** `UNIQUE (seat_id)` on bookings, which no isolation level can
  bypass.

Where SERIALIZABLE would be worth it: rules that span many rows and cannot
be expressed as a row lock or constraint, e.g. "a user may hold at most 10
seats across all events". There, a lock on one row does not help, and
SERIALIZABLE catches the write skew for you.
