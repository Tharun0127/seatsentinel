"""Shared fixtures.

Tests run against REAL Postgres and Redis (from docker compose) because the
bugs we care about (races, row locks, unique violations, SET NX) only exist
in the real systems. They never touch the app's data:
- Postgres: a separate `seatsentinel_test` database, migrated once per run
  and reset before every test.
- Redis: logical DB 15, flushed before every test.
"""
import asyncio
import os
from contextlib import AsyncExitStack
from datetime import datetime, timezone

import asyncpg
import bcrypt
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from app.auth import create_access_token
from app.config import Settings
from app.main import create_app
from app.strategies.common import StrategyDeps
from scripts.seed import seat_rows

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://seats:seats@localhost:5432/seatsentinel_test"
)
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6372/15")
PLAIN_DSN = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

EVENT_ID = 1
# Seat A1 of the test event; the 200-requests-for-one-seat tests aim here.
HOT_SEAT_ID = 1
TEST_PASSWORD = "password123"
# Hash once with a low cost; creating 200 users must not take a minute.
TEST_PASSWORD_HASH = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt(4)).decode()


def make_test_settings(**overrides) -> Settings:
    values = {
        "database_url": TEST_DATABASE_URL, "redis_url": TEST_REDIS_URL, "bcrypt_rounds": 4,
        # 200 concurrent requests in one process need plenty of connections.
        "db_pool_size": 20, "db_max_overflow": 230,
    }
    values.update(overrides)
    return Settings(**values)


async def _create_test_database() -> None:
    admin_dsn = PLAIN_DSN.rsplit("/", 1)[0] + "/postgres"
    db_name = PLAIN_DSN.rsplit("/", 1)[1]
    conn = await asyncpg.connect(admin_dsn)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name):
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_db() -> str:
    asyncio.run(_create_test_database())
    config = Config("alembic.ini")
    config.attributes["url"] = TEST_DATABASE_URL
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def db(migrated_db):
    """A raw asyncpg connection to the freshly reset test database."""
    conn = await asyncpg.connect(PLAIN_DSN)
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
    )
    await conn.execute(f"TRUNCATE {', '.join(t['tablename'] for t in tables)} RESTART IDENTITY CASCADE")
    await conn.execute(
        "INSERT INTO events (id, name, venue, starts_at) VALUES ($1, 'Test Gig', 'Test Hall', $2)",
        EVENT_ID, datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    await conn.executemany(
        "INSERT INTO seats (event_id, row_label, number, price) VALUES ($1, $2, $3, $4)", seat_rows(EVENT_ID)
    )
    yield conn
    await conn.close()


@pytest.fixture
async def redis_client():
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def make_users(db):
    """Create n users directly in the DB and return their auth headers."""
    settings = make_test_settings()

    async def make(n: int) -> list[dict]:
        rows = [(f"user{i}@test.dev", TEST_PASSWORD_HASH) for i in range(n)]
        ids = [
            r["id"] for r in await db.fetch(
                "INSERT INTO users (email, password_hash) SELECT * FROM unnest($1::text[], $2::text[]) RETURNING id",
                [r[0] for r in rows], [r[1] for r in rows],
            )
        ]
        return [{"Authorization": f"Bearer {create_access_token(settings, uid)}"} for uid in ids]

    return make


@pytest.fixture
async def client_factory(db, redis_client):
    async with AsyncExitStack() as stack:
        async def make(**overrides) -> AsyncClient:
            app = create_app(make_test_settings(**overrides))
            await stack.enter_async_context(LifespanManager(app))
            transport = ASGITransport(app=app)
            return await stack.enter_async_context(
                AsyncClient(transport=transport, base_url="http://test", timeout=60)
            )

        yield make


@pytest.fixture
async def client(client_factory) -> AsyncClient:
    return await client_factory()


def deps_for(client: AsyncClient) -> StrategyDeps:
    """The strategy dependencies of the app behind a test client."""
    state = client._transport.app.state
    return StrategyDeps(session_factory=state.session_factory, redis=state.redis, settings=state.settings)
