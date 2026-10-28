# SeatSentinel benchmarks

Setup: Windows 11 laptop, Docker Desktop (16 vCPU, 8 GB to the VM). API with
4 uvicorn workers, Postgres 16, Redis 7, all in Docker Compose. The load
generator ([`benchmarks/load.py`](../backend/benchmarks/load.py)) runs inside
the API container, so it shares CPU with the server. Numbers are the
**median of 3 runs**; correctness columns show the **worst** run.

Each run creates a temporary 1,000-seat "benchmark arena" event, resets it
before each strategy and deletes it afterwards, so real bookings are never
touched. Reproduce:

```bash
docker compose exec api python -m benchmarks.load --mode contended --attempts 2000
docker compose exec api python -m benchmarks.load --mode distinct --attempts 1000
```

## Comparison

| Strategy | Correct? (hot seat: winners of 200) | Double-booked seats under load | Throughput (contended) | p95 (contended) | Throughput (no contention) | p95 (no contention) | When to use it |
|---|---|---|---|---|---|---|---|
| **naive** | **No: 4 winners** | **3** | 328 req/s | 552 ms | 395 req/s | 416 ms | Never. Kept as the broken demo. |
| **pessimistic** (`FOR UPDATE`) | Yes: 1 | 0 | 377 req/s | 396 ms | 238 req/s | 612 ms | High contention on the same rows (hot seats, inventory counters). Simple and predictable; waiters queue instead of failing. |
| **optimistic** (version) | Yes: 1 | 0 | 381 req/s | 402 ms | 288 req/s | 556 ms | Low-to-medium contention, or long "think time" between read and write (e.g. a form). No locks held, but wasted work when conflicts are common. |
| **redis lock** (`SET NX PX`) | Yes: 1 | 0 | 367 req/s | 434 ms | 295 req/s | 572 ms | When the critical section spans more than one DB row or system (external API, several databases), or to keep lock queues out of Postgres. Needs the DB constraint as a fence. |

All runs: **0 server errors (5xx)** for every strategy.

### Raw results

Contended: 2,000 booking attempts, 50 concurrent users, half the picks on
the 100 front seats ([`load_contended.md`](../backend/benchmarks/results/load_contended.md)).

| Strategy | Hot seat: winners of 200 | Double-booked seats | Throughput | p50 | p95 | p99 | Booked / 409 / 5xx |
|---|---|---|---|---|---|---|---|
| naive | 4 | 3 | 328.3 req/s | 78.5 ms | 552.4 ms | 804.3 ms | 669 / 1331 / 0 |
| pessimistic | 1 | 0 | 377.4 req/s | 79.9 ms | 395.8 ms | 670.0 ms | 688 / 1312 / 0 |
| optimistic | 1 | 0 | 381.0 req/s | 80.0 ms | 401.6 ms | 623.5 ms | 673 / 1327 / 0 |
| redis | 1 | 0 | 366.9 req/s | 79.1 ms | 434.0 ms | 681.1 ms | 677 / 1323 / 0 |

No contention: 1,000 attempts, every request a different seat
([`load_distinct.md`](../backend/benchmarks/results/load_distinct.md)).

| Strategy | Hot seat: winners of 200 | Double-booked seats | Throughput | p50 | p95 | p99 | Booked / 409 / 5xx |
|---|---|---|---|---|---|---|---|
| naive | 3 | 0 | 395.3 req/s | 22.9 ms | 415.8 ms | 832.8 ms | 1000 / 0 / 0 |
| pessimistic | 1 | 0 | 237.7 req/s | 132.5 ms | 611.9 ms | 954.3 ms | 1000 / 0 / 0 |
| optimistic | 1 | 0 | 287.5 req/s | 103.8 ms | 556.0 ms | 878.1 ms | 1000 / 0 / 0 |
| redis | 1 | 0 | 294.7 req/s | 96.8 ms | 572.3 ms | 826.3 ms | 1000 / 0 / 0 |

## What the numbers say

- **Correctness is the real difference.** Naive sold seats twice in every
  run, and 2 to 4 users "won" the hot seat. Every correct strategy had
  exactly 1 winner and 0 duplicates, in every run.
- **With no contention, naive is fastest** (it skips locking), and the
  correct strategies cost roughly 25 to 40% throughput. Pessimistic is the
  slowest: `SET LOCAL lock_timeout` plus `FOR UPDATE` cost an extra round
  trip and a row lock per request even when nobody competes.
- **Under contention, the correct strategies are all about the same, and
  slightly faster than naive.** A losing request ends early with a cheap 409
  (pessimistic re-reads a booked row, optimistic sees "booked" on re-read,
  Redis rejects at `SET NX` without touching Postgres). Naive's losers do
  the same work, and its winners write more rows.
- **Pessimistic did not suffer from lock queues here** because only seats in
  demand at the same instant conflict, and each critical section is a few
  milliseconds. Long transactions (e.g. calling a payment provider while
  holding the lock) would change that dramatically; that is why payment
  happens outside the booking transaction in this project.
- **The API is CPU-bound in Python at ~250 to 400 req/s on this laptop.** The
  latency tails (p95 400 to 600 ms) are mostly queueing: 50 concurrent users
  divided by ~300 req/s is about 170 ms average wait. Differences of ±10%
  between correct strategies are within run-to-run noise (single runs
  varied by up to 1.7x), which is why we report medians.

## The 200-requests-for-one-seat test

Also part of the test suite (`tests/test_strategies.py`, in-process with a
warm pool):

| Strategy | Result of 200 concurrent requests for seat A1 |
|---|---|
| naive | 20 users told "booked", 20 rows in `naive_bookings` |
| naive logic against the real `bookings` table | 1 booked, the rest blocked by `UNIQUE(seat_id)` |
| pessimistic | `{201: 1, 409: 199}`, 1 row |
| optimistic | `{201: 1, 409: 199}`, 1 row |
| redis | `{201: 1, 409: 199}`, 1 row |

The naive winner count is lower against the live server (2 to 4) than in the
test (20) because the load script sends from one Python process and requests
arrive slightly staggered. Any number above 1 is a bug.
