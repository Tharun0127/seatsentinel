"""POST /book: book seats directly using a chosen concurrency strategy.

This is the endpoint the race-condition demos and the load test use. The
product flow (hold -> pay -> confirm) reuses the same strategy modules.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.strategies import naive, optimistic, pessimistic, redis_lock
from app.strategies.common import BookingError, BookingRequest, StrategyDeps

router = APIRouter(tags=["booking"])

STRATEGIES = {
    naive.NAME: naive.book,  # BROKEN demo, see app/strategies/naive.py
    pessimistic.NAME: pessimistic.book,
    optimistic.NAME: optimistic.book,
    redis_lock.NAME: redis_lock.book,
}
StrategyName = Literal["naive", "pessimistic", "optimistic", "redis"]


class BookBody(BaseModel):
    event_id: int
    seat_ids: list[int] = Field(min_length=1, max_length=10)
    strategy: StrategyName = "pessimistic"


class BookResponse(BaseModel):
    reference: str
    seat_ids: list[int]
    strategy: str
    attempts: int


def strategy_deps(request: Request) -> StrategyDeps:
    state = request.app.state
    return StrategyDeps(session_factory=state.session_factory, redis=state.redis, settings=state.settings)


@router.post("/book", response_model=BookResponse, status_code=201)
async def book(body: BookBody, request: Request, user_id: int = Depends(current_user_id)):
    booking = BookingRequest(user_id=user_id, event_id=body.event_id, seat_ids=body.seat_ids)
    try:
        result = await STRATEGIES[body.strategy](strategy_deps(request), booking)
    except BookingError as exc:
        raise HTTPException(exc.status_code, str(exc))
    return BookResponse(reference=str(result.reference), seat_ids=result.seat_ids,
                        strategy=result.strategy, attempts=result.attempts)
