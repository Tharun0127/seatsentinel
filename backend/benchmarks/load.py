"""Load test: many users booking seats across a big map, once per strategy.

Run inside the api container (the API must be up with its 4 workers):
    python -m benchmarks.load
    python -m benchmarks.load --attempts 2000 --concurrency 50

What it does, per strategy (naive, pessimistic, optimistic, redis):
1. Reset a temporary "benchmark arena" event (40 rows x 25 seats = 1,000
   seats) that the script creates and deletes itself, so real bookings are
   never touched.
2. LOAD: `attempts` booking requests from `concurrency` users at once.
   --mode contended (default) or --mode distinct (every request a new seat).
   Each picks a random seat; half of the picks go to the 100 front seats
   (everyone wants the front), so contention grows as the map fills.
3. HOT SEAT: 200 different users request the SAME seat at the same moment.
4. Verify in Postgres: double bookings (seats sold more than once).

Why a script and not Locust: every strategy needs a fresh map, exact
counts of 201/409/5xx, and a database check afterwards. A short asyncio
script does that with less moving parts.
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import time
from dataclasses import asdict, dataclass

import asyncpg
import bcrypt
import httpx

from app.auth import create_access_token
from app.config import get_settings

API_URL = os.getenv("BENCH_API_URL", "http://localhost:8000")
DSN = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
EVENT_NAME = "Benchmark Arena (temporary)"
ROWS, SEATS_PER_ROW = 40, 25
STRATEGIES = ["naive", "pessimistic", "optimistic", "redis"]
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


@dataclass
class RunResult:
    strategy: str
    attempts: int
    successes: int
    conflicts: int
    errors: int
    throughput_rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    double_booked_seats: int
    hot_seat_winners: int
    hot_seat_errors: int


# --- setup ---------------------------------------------------------------------
async def create_arena(conn: asyncpg.Connection) -> tuple[int, list[int]]:
    await drop_arena(conn)
    event_id = await conn.fetchval(
        "INSERT INTO events (name, venue, starts_at) VALUES ($1, 'Load test', now()) RETURNING id", EVENT_NAME
    )
    # row_label is one character, so 40 rows reuse letters; seat numbers are
    # unique across the arena, which keeps (event, row, number) unique.
    await conn.executemany(
        "INSERT INTO seats (event_id, row_label, number, price) VALUES ($1, $2, $3, 10)",
        [(event_id, chr(65 + r % 26), r * SEATS_PER_ROW + n) for r in range(ROWS) for n in range(1, SEATS_PER_ROW + 1)],
    )
    seat_ids = [r["id"] for r in await conn.fetch("SELECT id FROM seats WHERE event_id = $1 ORDER BY id", event_id)]
    return event_id, seat_ids


async def drop_arena(conn: asyncpg.Connection) -> None:
    event_id = await conn.fetchval("SELECT id FROM events WHERE name = $1", EVENT_NAME)
    if event_id is None:
        return
    await conn.execute("DELETE FROM bookings WHERE event_id = $1", event_id)
    await conn.execute("DELETE FROM naive_bookings WHERE event_id = $1", event_id)
    await conn.execute("DELETE FROM seats WHERE event_id = $1", event_id)
    await conn.execute("DELETE FROM events WHERE id = $1", event_id)


async def reset_arena(conn: asyncpg.Connection, event_id: int) -> None:
    await conn.execute("DELETE FROM bookings WHERE event_id = $1", event_id)
    await conn.execute("DELETE FROM naive_bookings WHERE event_id = $1", event_id)
    await conn.execute(
        "UPDATE seats SET status = 'available', booked_by = NULL, version = 0 WHERE event_id = $1", event_id
    )


async def create_users(conn: asyncpg.Connection, n: int) -> list[dict]:
    password_hash = bcrypt.hashpw(b"loadtest-password", bcrypt.gensalt(4)).decode()
    emails = [f"loadtest-{i}@bench.local" for i in range(n)]
    await conn.execute(
        "INSERT INTO users (email, password_hash) SELECT unnest($1::text[]), $2 ON CONFLICT (email) DO NOTHING",
        emails, password_hash,
    )
    ids = [r["id"] for r in await conn.fetch("SELECT id FROM users WHERE email = ANY($1::text[])", emails)]
    settings = get_settings()
    return [{"Authorization": f"Bearer {create_access_token(settings, uid)}"} for uid in ids]


# --- load ------------------------------------------------------------------------
def pick_hot_biased(seat_ids: list[int], _attempt: int) -> int:
    # Half the demand goes to the 100 best seats, like a real on-sale.
    return random.choice(seat_ids[:100]) if random.random() < 0.5 else random.choice(seat_ids)


def pick_distinct(seat_ids: list[int], attempt: int) -> int:
    # Every request a different seat: zero contention, pure strategy overhead.
    return seat_ids[attempt % len(seat_ids)]


PICKERS = {"contended": pick_hot_biased, "distinct": pick_distinct}


async def mixed_load(client, users, event_id, seat_ids, strategy, attempts, concurrency, pick):
    latencies, statuses = [], []
    queue = asyncio.Queue()
    for i in range(attempts):
        queue.put_nowait(i)

    async def worker(headers):
        while not queue.empty():
            attempt = queue.get_nowait()
            body = {"event_id": event_id, "seat_ids": [pick(seat_ids, attempt)], "strategy": strategy}
            started = time.perf_counter()
            try:
                response = await client.post("/book", json=body, headers=headers)
                statuses.append(response.status_code)
            except httpx.HTTPError as exc:
                # 599 = the request never got an HTTP answer (client-side error).
                print(f"client error: {type(exc).__name__}: {exc}")
                statuses.append(599)
            latencies.append((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    await asyncio.gather(*(worker(users[i % len(users)]) for i in range(concurrency)))
    return latencies, statuses, time.perf_counter() - started


async def hot_seat(client, users, event_id, seat_id, strategy):
    body = {"event_id": event_id, "seat_ids": [seat_id], "strategy": strategy}
    responses = await asyncio.gather(
        *(client.post("/book", json=body, headers=h) for h in users[:200]), return_exceptions=True
    )
    codes = [r.status_code if isinstance(r, httpx.Response) else 599 for r in responses]
    return codes.count(201), sum(c >= 500 for c in codes)


async def double_booked(conn, event_id: int, strategy: str) -> int:
    table = "naive_bookings" if strategy == "naive" else "bookings"
    return await conn.fetchval(
        f"SELECT count(*) FROM (SELECT seat_id FROM {table} WHERE event_id = $1"
        " GROUP BY seat_id HAVING count(*) > 1) d", event_id,
    )


def percentile(values: list[float], pct: float) -> float:
    return round(statistics.quantiles(values, n=100)[int(pct) - 1], 1)


async def run(attempts: int, concurrency: int, mode: str) -> list[RunResult]:
    conn = await asyncpg.connect(DSN)
    event_id, seat_ids = await create_arena(conn)
    users = await create_users(conn, 200)
    # keepalive_expiry below uvicorn's 5 s keep-alive timeout: otherwise the
    # client can reuse a connection the server is closing at that instant,
    # and the request fails without ever reaching the API.
    limits = httpx.Limits(max_connections=250, max_keepalive_connections=250, keepalive_expiry=2.0)
    results = []
    try:
        async with httpx.AsyncClient(base_url=API_URL, limits=limits, timeout=60) as client:
            for strategy in STRATEGIES:
                await reset_arena(conn, event_id)
                latencies, statuses, elapsed = await mixed_load(
                    client, users, event_id, seat_ids, strategy, attempts, concurrency, PICKERS[mode]
                )
                doubles = await double_booked(conn, event_id, strategy)

                await reset_arena(conn, event_id)
                winners, hot_errors = await hot_seat(client, users, event_id, seat_ids[0], strategy)

                result = RunResult(
                    strategy=strategy, attempts=attempts, successes=statuses.count(201),
                    conflicts=statuses.count(409), errors=sum(s >= 500 for s in statuses),
                    throughput_rps=round(attempts / elapsed, 1), p50_ms=percentile(latencies, 50),
                    p95_ms=percentile(latencies, 95), p99_ms=percentile(latencies, 99),
                    double_booked_seats=doubles, hot_seat_winners=winners, hot_seat_errors=hot_errors,
                )
                print(json.dumps(asdict(result)))
                results.append(result)
    finally:
        await drop_arena(conn)
        await conn.close()
    return results


def to_markdown(results: list[RunResult]) -> str:
    lines = [
        "| Strategy | Hot seat: winners of 200 | Double-booked seats (mixed load) | Throughput | p50 | p95 | p99 "
        "| Booked / 409 / 5xx |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.strategy} | {r.hot_seat_winners} | {r.double_booked_seats} | {r.throughput_rps} req/s "
            f"| {r.p50_ms} ms | {r.p95_ms} ms | {r.p99_ms} ms | {r.successes} / {r.conflicts} / {r.errors} |"
        )
    return "\n".join(lines)


def median_of(runs: list[list[RunResult]]) -> list[RunResult]:
    """Median per metric across repeated runs (laptop benchmarks are noisy).

    Correctness numbers take the WORST run instead: one double booking in
    any run is a failure, however good the median looks.
    """
    merged = []
    for per_strategy in zip(*runs):
        first = per_strategy[0]
        med = lambda field: statistics.median(getattr(r, field) for r in per_strategy)  # noqa: E731
        worst = lambda field: max(getattr(r, field) for r in per_strategy)  # noqa: E731
        merged.append(RunResult(
            strategy=first.strategy, attempts=first.attempts, successes=round(med("successes")),
            conflicts=round(med("conflicts")), errors=worst("errors"), throughput_rps=med("throughput_rps"),
            p50_ms=med("p50_ms"), p95_ms=med("p95_ms"), p99_ms=med("p99_ms"),
            double_booked_seats=worst("double_booked_seats"), hot_seat_winners=worst("hot_seat_winners"),
            hot_seat_errors=worst("hot_seat_errors"),
        ))
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--mode", choices=sorted(PICKERS), default="contended",
                        help="contended: half the demand on 100 front seats; distinct: every request a new seat")
    parser.add_argument("--repeat", type=int, default=3, help="runs to take the median of")
    args = parser.parse_args()
    runs = [asyncio.run(run(args.attempts, args.concurrency, args.mode)) for _ in range(args.repeat)]
    results = median_of(runs)
    table = to_markdown(results)
    print("\n" + table)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, f"load_{args.mode}.md"), "w") as handle:
        handle.write(table + "\n")
    with open(os.path.join(RESULTS_DIR, f"load_{args.mode}.json"), "w") as handle:
        json.dump([asdict(r) for r in results], handle, indent=2)


if __name__ == "__main__":
    main()
