"""READ COMMITTED vs SERIALIZABLE, shown with two people booking one seat.

Run inside the api container:
    python -m scripts.isolation_demo

It uses its own scratch table (isolation_demo_seats), never the app's data.
Two connections play Alice and Bob. The steps are interleaved by hand so the
race happens every time:

    Alice: BEGIN; SELECT status -> 'available'
    Bob:   BEGIN; SELECT status -> 'available'
    Alice: UPDATE -> booked by alice; COMMIT
    Bob:   UPDATE -> booked by bob;   COMMIT ?

Scenarios:
1. READ COMMITTED (Postgres default), blind UPDATE: Bob's UPDATE succeeds and
   silently overwrites Alice. Both were told "booked". Lost update.
2. SERIALIZABLE, same code: Bob's UPDATE fails with a serialization error
   (SQLSTATE 40001). The app must catch it and retry or report "taken".
3. READ COMMITTED with a conditional UPDATE (WHERE status = 'available'):
   Bob's UPDATE matches 0 rows. Correct without raising the isolation level.
   This is what the optimistic strategy does.
"""
import asyncio
import os

import asyncpg

TABLE = "isolation_demo_seats"


async def setup(conn: asyncpg.Connection) -> None:
    await conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (id int PRIMARY KEY, status text, booked_by text)")
    await conn.execute(f"TRUNCATE {TABLE}")
    await conn.execute(f"INSERT INTO {TABLE} VALUES (1, 'available', NULL)")


async def scenario(dsn: str, isolation: str, conditional_update: bool) -> dict:
    """Play the interleaving above. Returns what each side saw and the final row."""
    alice, bob = await asyncpg.connect(dsn), await asyncpg.connect(dsn)
    log: list[str] = []
    outcome = {"isolation": isolation, "conditional_update": conditional_update}
    try:
        await setup(alice)
        alice_tx = alice.transaction(isolation=isolation)
        bob_tx = bob.transaction(isolation=isolation)
        await alice_tx.start()
        await bob_tx.start()

        a_status = await alice.fetchval(f"SELECT status FROM {TABLE} WHERE id = 1")
        b_status = await bob.fetchval(f"SELECT status FROM {TABLE} WHERE id = 1")
        log.append(f"Alice reads '{a_status}', Bob reads '{b_status}'")

        guard = " AND status = 'available'" if conditional_update else ""
        update = f"UPDATE {TABLE} SET status = 'booked', booked_by = $1 WHERE id = 1{guard}"

        await alice.execute(update, "alice")
        await alice_tx.commit()
        log.append("Alice books it and commits")

        try:
            result = await bob.execute(update, "bob")
            rows = int(result.split()[-1])
            await bob_tx.commit()
            outcome["bob"] = "booked" if rows == 1 else "0 rows updated -> seat taken"
            log.append(f"Bob's UPDATE: {result} -> commit OK")
        except asyncpg.exceptions.SerializationError as exc:
            await bob_tx.rollback()
            outcome["bob"] = f"serialization error {exc.sqlstate}"
            log.append(f"Bob's UPDATE fails: {exc.sqlstate} {exc}")

        row = await alice.fetchrow(f"SELECT status, booked_by FROM {TABLE} WHERE id = 1")
        outcome["final_booked_by"] = row["booked_by"]
        outcome["both_told_booked"] = outcome["bob"] == "booked"
        log.append(f"Final row: {dict(row)}")
        outcome["log"] = log
    finally:
        await alice.execute(f"DROP TABLE IF EXISTS {TABLE}")
        await alice.close()
        await bob.close()
    return outcome


SCENARIOS = [
    ("1. READ COMMITTED, blind UPDATE", "read_committed", False),
    ("2. SERIALIZABLE, blind UPDATE", "serializable", False),
    ("3. READ COMMITTED, conditional UPDATE", "read_committed", True),
]


async def main() -> None:
    dsn = os.getenv("DATABASE_URL", "postgresql://seats:seats@localhost:5432/seatsentinel")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    for title, isolation, conditional in SCENARIOS:
        outcome = await scenario(dsn, isolation, conditional)
        print(f"\n{title}")
        for line in outcome["log"]:
            print(f"   {line}")
        verdict = "DOUBLE BOOKED (lost update)" if outcome["both_told_booked"] else "correct: only Alice has it"
        print(f"   => {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
