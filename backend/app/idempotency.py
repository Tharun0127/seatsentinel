"""Idempotency keys for the payment and confirm endpoints.

The client sends `Idempotency-Key: <uuid>` and reuses it when it retries
(timeout, flaky network, double-click). We guarantee the work runs at most
once per key, and every retry gets the ORIGINAL response back.

How:
1. INSERT (user, endpoint, key, status='in_progress') ON CONFLICT DO NOTHING.
   The primary key makes this an atomic "claim": of 20 identical requests
   arriving together, exactly one inserts the row.
2. The winner does the work, then stores the response (even a 409) and
   marks the key 'completed'.
3. Anyone else finds the existing row:
   - completed, same request  -> replay the stored response
   - completed, different body -> 422 (the key was reused for something else)
   - still in_progress         -> 409, "retry in a moment"

Stored in Postgres, not Redis: an idempotency record protects money, so it
must survive a Redis restart and live as long as the payment itself.
"""
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import IdempotencyKey

IN_PROGRESS = "in_progress"
COMPLETED = "completed"


@dataclass
class StoredResponse:
    status_code: int
    body: dict
    replayed: bool = False


Handler = Callable[[], Awaitable[StoredResponse]]


def hash_request(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def require_key(key: str | None) -> str:
    if not key or len(key) > 255:
        raise HTTPException(400, "Idempotency-Key header is required (max 255 chars)")
    return key


async def run_once(
    session_factory: async_sessionmaker, user_id: int, endpoint: str, key: str, body: dict, handler: Handler
) -> StoredResponse:
    request_hash = hash_request(body)
    ids = {"user_id": user_id, "endpoint": endpoint, "key": key}

    async with session_factory() as session, session.begin():
        claimed = await session.scalar(
            insert(IdempotencyKey)
            .values(**ids, request_hash=request_hash, status=IN_PROGRESS)
            .on_conflict_do_nothing()
            .returning(IdempotencyKey.key)
        )
    if claimed is None:
        return await replay(session_factory, ids, request_hash)

    try:
        response = await handler()
    except Exception:
        # Unexpected failure (a 500): forget the key so the client may retry.
        # Expected failures (409 etc.) come back as StoredResponse and stick.
        async with session_factory() as session, session.begin():
            await session.execute(delete(IdempotencyKey).filter_by(**ids))
        raise

    async with session_factory() as session, session.begin():
        await session.execute(
            update(IdempotencyKey).filter_by(**ids)
            .values(status=COMPLETED, response_status=response.status_code, response_body=response.body)
        )
    return response


async def replay(session_factory: async_sessionmaker, ids: dict, request_hash: str) -> StoredResponse:
    async with session_factory() as session:
        record = await session.scalar(select(IdempotencyKey).filter_by(**ids))
    if record is None:  # the first request failed and released the key
        raise HTTPException(409, "previous attempt with this Idempotency-Key failed; please retry")
    if record.request_hash != request_hash:
        raise HTTPException(422, "Idempotency-Key was already used with a different request body")
    if record.status == IN_PROGRESS:
        raise HTTPException(409, "a request with this Idempotency-Key is still being processed")
    return StoredResponse(record.response_status, record.response_body, replayed=True)
