"""Strategy name -> booking function. One place, used by /book and /confirm."""
from typing import Literal

from app.strategies import naive, optimistic, pessimistic, redis_lock

STRATEGIES = {
    naive.NAME: naive.book,  # BROKEN demo, see app/strategies/naive.py
    pessimistic.NAME: pessimistic.book,
    optimistic.NAME: optimistic.book,
    redis_lock.NAME: redis_lock.book,
}

StrategyName = Literal["naive", "pessimistic", "optimistic", "redis"]
# The real checkout must never use the broken demo.
SafeStrategyName = Literal["pessimistic", "optimistic", "redis"]
