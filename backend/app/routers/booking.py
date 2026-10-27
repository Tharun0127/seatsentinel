"""Booking endpoints.

- POST /book                 direct booking with a chosen strategy (race demos, load test)
- POST /events/{id}/holds    hold seats for 5 minutes
- DELETE /events/{id}/holds  release my holds
- POST /payments             mock charge for held seats       (Idempotency-Key required)
- POST /bookings/confirm     turn a payment into a booking    (Idempotency-Key required)
- GET  /bookings/me          my bookings
"""
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import checkout, holds, idempotency
from app.auth import current_user_id
from app.models import Booking, Event, Payment, Seat
from app.strategies.common import BookingError, BookingRequest, StrategyDeps
from app.strategies.registry import STRATEGIES, SafeStrategyName, StrategyName

router = APIRouter(tags=["booking"])


def strategy_deps(request: Request) -> StrategyDeps:
    state = request.app.state
    return StrategyDeps(session_factory=state.session_factory, redis=state.redis, settings=state.settings)


# --- direct booking ------------------------------------------------------------
class BookBody(BaseModel):
    event_id: int
    seat_ids: list[int] = Field(min_length=1, max_length=10)
    strategy: StrategyName = "pessimistic"


class BookResponse(BaseModel):
    reference: str
    seat_ids: list[int]
    strategy: str
    attempts: int


@router.post("/book", response_model=BookResponse, status_code=201)
async def book(body: BookBody, deps: StrategyDeps = Depends(strategy_deps), user_id: int = Depends(current_user_id)):
    booking = BookingRequest(user_id=user_id, event_id=body.event_id, seat_ids=body.seat_ids)
    # Respect other users' holds. This check is advisory (it is itself
    # check-then-act); the strategy below is what guarantees correctness.
    if holds.held_by_others(await holds.current(deps.redis, booking.seat_ids), user_id):
        raise HTTPException(409, "seat is held by another customer")
    try:
        result = await STRATEGIES[body.strategy](deps, booking)
    except BookingError as exc:
        raise HTTPException(exc.status_code, str(exc))
    return BookResponse(reference=str(result.reference), seat_ids=result.seat_ids,
                        strategy=result.strategy, attempts=result.attempts)


# --- holds ---------------------------------------------------------------------
class HoldBody(BaseModel):
    seat_ids: list[int] = Field(min_length=1, max_length=10)


@router.post("/events/{event_id}/holds", status_code=201)
async def hold_seats(event_id: int, body: HoldBody, deps: StrategyDeps = Depends(strategy_deps),
                     user_id: int = Depends(current_user_id)):
    seat_ids = sorted(set(body.seat_ids))
    async with deps.session_factory() as session:
        seats = (await session.scalars(select(Seat).where(Seat.event_id == event_id, Seat.id.in_(seat_ids)))).all()
    if len(seats) != len(seat_ids):
        raise HTTPException(404, "one or more seats do not exist for this event")
    booked = [f"{s.row_label}{s.number}" for s in seats if s.status != "available"]
    if booked:
        raise HTTPException(409, f"seat(s) {', '.join(booked)} already booked")

    taken = await holds.acquire(deps.redis, user_id, seat_ids, deps.settings.hold_ttl_seconds)
    if taken is not None:
        seat = next(s for s in seats if s.id == taken)
        raise HTTPException(409, f"sorry, seat {seat.row_label}{seat.number} was just taken by someone else")
    return {"seat_ids": seat_ids, "expires_in_ms": int(deps.settings.hold_ttl_seconds * 1000)}


@router.delete("/events/{event_id}/holds", status_code=200)
async def release_holds(event_id: int, body: HoldBody, deps: StrategyDeps = Depends(strategy_deps),
                        user_id: int = Depends(current_user_id)):
    released = await holds.release(deps.redis, user_id, sorted(set(body.seat_ids)))
    return {"released": released}


# --- payment and confirm (idempotent) ----------------------------------------
class PaymentBody(BaseModel):
    event_id: int
    seat_ids: list[int] = Field(min_length=1, max_length=10)
    # Mock card form fields; never stored. Real card data would go to a PSP.
    card_last4: str = Field(default="4242", pattern=r"^\d{4}$")


class ConfirmBody(BaseModel):
    payment_id: uuid.UUID
    strategy: SafeStrategyName = "pessimistic"


def to_response(stored: idempotency.StoredResponse) -> JSONResponse:
    headers = {"Idempotent-Replayed": "true"} if stored.replayed else {}
    return JSONResponse(status_code=stored.status_code, content=stored.body, headers=headers)


@router.post("/payments", status_code=201)
async def pay(body: PaymentBody, deps: StrategyDeps = Depends(strategy_deps), user_id: int = Depends(current_user_id),
              idempotency_key: str | None = Header(default=None)):
    key = idempotency.require_key(idempotency_key)
    stored = await idempotency.run_once(
        deps.session_factory, user_id, "payments", key, body.model_dump(mode="json"),
        lambda: checkout.charge(deps, user_id, body.event_id, body.seat_ids),
    )
    return to_response(stored)


@router.post("/bookings/confirm", status_code=201)
async def confirm(body: ConfirmBody, deps: StrategyDeps = Depends(strategy_deps),
                  user_id: int = Depends(current_user_id), idempotency_key: str | None = Header(default=None)):
    key = idempotency.require_key(idempotency_key)
    stored = await idempotency.run_once(
        deps.session_factory, user_id, "bookings/confirm", key, body.model_dump(mode="json"),
        lambda: checkout.confirm(deps, user_id, body.payment_id, body.strategy),
    )
    return to_response(stored)


@router.get("/bookings/me")
async def my_bookings(deps: StrategyDeps = Depends(strategy_deps), user_id: int = Depends(current_user_id)):
    query = (
        select(Booking.reference, Booking.created_at, Booking.strategy, Event.name, Event.starts_at,
               Seat.row_label, Seat.number, Seat.price, Payment.amount)
        .join(Seat, Seat.id == Booking.seat_id)
        .join(Event, Event.id == Booking.event_id)
        .outerjoin(Payment, Payment.booking_reference == Booking.reference)
        .where(Booking.user_id == user_id)
        .order_by(Booking.created_at.desc(), Seat.row_label, Seat.number)
    )
    async with deps.session_factory() as session:
        rows = (await session.execute(query)).all()
    grouped: dict = {}
    for r in rows:
        entry = grouped.setdefault(str(r.reference), {
            "reference": str(r.reference), "event_name": r.name, "starts_at": r.starts_at.isoformat(),
            "booked_at": r.created_at.isoformat(), "strategy": r.strategy, "seats": [],
            "amount": str(r.amount) if r.amount is not None else None, "_total": 0,
        })
        entry["seats"].append(f"{r.row_label}{r.number}")
        entry["_total"] += r.price
    for entry in grouped.values():
        # Direct /book bookings have no payment; show the seat price total.
        entry["amount"] = entry["amount"] or str(entry["_total"])
        del entry["_total"]
    return list(grouped.values())
