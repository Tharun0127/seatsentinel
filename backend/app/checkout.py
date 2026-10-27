"""The product flow after seats are held: charge (mock), then confirm.

Both functions return a StoredResponse instead of raising, because the
idempotency layer must store and replay failures (like a 409) too.
"""
import uuid
from decimal import Decimal

from sqlalchemy import func, select, update

from app import holds
from app.models import Payment, Seat
from app.strategies.common import BookingError, BookingRequest, StrategyDeps
from app.strategies.registry import STRATEGIES
from app.idempotency import StoredResponse

CHARGED, CONFIRMING, CONFIRMED, REFUNDED = "charged", "confirming", "confirmed", "refunded"


def error(status_code: int, detail: str) -> StoredResponse:
    return StoredResponse(status_code, {"detail": detail})


async def seat_labels(deps: StrategyDeps, seat_ids: list[int]) -> dict[int, str]:
    async with deps.session_factory() as session:
        rows = await session.execute(select(Seat.id, Seat.row_label, Seat.number).where(Seat.id.in_(seat_ids)))
    return {seat_id: f"{row}{number}" for seat_id, row, number in rows}


def describe(labels: dict[int, str], seat_ids: list[int]) -> str:
    return ", ".join(labels.get(s, str(s)) for s in seat_ids)


# --- charge ------------------------------------------------------------------
async def charge(deps: StrategyDeps, user_id: int, event_id: int, seat_ids: list[int]) -> StoredResponse:
    """Mock card charge for seats the user currently holds."""
    seat_ids = sorted(set(seat_ids))
    current = await holds.current(deps.redis, seat_ids)
    not_mine = [s for s in seat_ids if s not in current or current[s].user_id != user_id]
    if not_mine:
        labels = await seat_labels(deps, not_mine)
        return error(409, f"you are not holding seat(s) {describe(labels, not_mine)}; select them again")

    async with deps.session_factory() as session, session.begin():
        prices = (await session.scalars(
            select(Seat.price).where(Seat.event_id == event_id, Seat.id.in_(seat_ids))
        )).all()
        if len(prices) != len(seat_ids):
            return error(404, "one or more seats do not exist for this event")
        payment = Payment(user_id=user_id, event_id=event_id, seat_ids=seat_ids,
                          amount=sum(prices, Decimal("0")), status=CHARGED)
        session.add(payment)
    return StoredResponse(201, payment_body(payment))


def payment_body(payment: Payment) -> dict:
    return {"payment_id": str(payment.id), "event_id": payment.event_id, "seat_ids": payment.seat_ids,
            "amount": str(payment.amount), "status": payment.status}


# --- confirm -----------------------------------------------------------------
async def confirm(deps: StrategyDeps, user_id: int, payment_id: uuid.UUID, strategy: str) -> StoredResponse:
    payment = await claim_payment(deps, user_id, payment_id)
    if isinstance(payment, StoredResponse):
        return payment  # could not claim: already used, refunded, or not found

    late_recovery, lost = await secure_holds(deps, user_id, payment.seat_ids)
    if lost:
        labels = await seat_labels(deps, lost)
        await set_payment_status(deps, payment.id, REFUNDED)
        return error(409, f"your hold expired and seat(s) {describe(labels, lost)} were taken by someone "
                          f"else. Your payment of {payment.amount} has been refunded.")

    request = BookingRequest(user_id=user_id, event_id=payment.event_id, seat_ids=payment.seat_ids)
    try:
        await STRATEGIES[strategy](deps, request)
    except BookingError as exc:
        await set_payment_status(deps, payment.id, REFUNDED)
        return error(409, f"{exc}. Your payment of {payment.amount} has been refunded.")

    await set_payment_status(deps, payment.id, CONFIRMED, booking_reference=request.reference)
    await holds.release(deps.redis, user_id, payment.seat_ids)
    return StoredResponse(201, {
        "reference": str(request.reference), "payment_id": str(payment.id), "event_id": payment.event_id,
        "seat_ids": payment.seat_ids, "amount": str(payment.amount), "strategy": strategy,
        "late_hold_recovered": late_recovery,
    })


async def claim_payment(deps: StrategyDeps, user_id: int, payment_id: uuid.UUID) -> Payment | StoredResponse:
    """charged -> confirming, as one compare-and-set UPDATE.

    Two confirm requests with DIFFERENT idempotency keys for the same payment
    both get here; only one UPDATE can match status='charged'.
    """
    async with deps.session_factory() as session, session.begin():
        payment = await session.scalar(
            update(Payment)
            .where(Payment.id == payment_id, Payment.user_id == user_id, Payment.status == CHARGED)
            .values(status=CONFIRMING, updated_at=func.now())
            .returning(Payment)
        )
        if payment is not None:
            return payment
        existing = await session.scalar(
            select(Payment).where(Payment.id == payment_id, Payment.user_id == user_id)
        )
    if existing is None:
        return error(404, "payment not found")
    if existing.status == CONFIRMED:
        return error(409, f"payment already used for booking {existing.booking_reference}")
    if existing.status == REFUNDED:
        return error(409, "payment was refunded and cannot be used")
    return error(409, "this payment is already being confirmed")


async def secure_holds(deps: StrategyDeps, user_id: int, seat_ids: list[int]) -> tuple[bool, list[int]]:
    """Make sure the user still holds every paid seat.

    The tricky edge case: payment succeeded, but the 5-minute hold ran out a
    moment before confirm. If nobody else has taken the seat, we simply hold
    it again and carry on (the customer paid; do not punish them for a few
    seconds). If someone else holds it now, the customer has lost it.

    Returns (late_recovery_happened, seats_lost_to_someone_else).
    """
    current = await holds.current(deps.redis, seat_ids)
    lost = holds.held_by_others(current, user_id)
    if lost:
        return False, lost
    expired = [s for s in seat_ids if s not in current]
    if not expired:
        return False, []
    # Atomic re-hold: fails if someone grabbed the seat in the meantime.
    taken = await holds.acquire(deps.redis, user_id, expired, deps.settings.hold_ttl_seconds)
    return (False, [taken]) if taken is not None else (True, [])


async def set_payment_status(deps: StrategyDeps, payment_id: uuid.UUID, status: str,
                             booking_reference: uuid.UUID | None = None) -> None:
    values = {"status": status, "updated_at": func.now()}
    if booking_reference is not None:
        values["booking_reference"] = booking_reference
    async with deps.session_factory() as session, session.begin():
        await session.execute(update(Payment).where(Payment.id == payment_id).values(**values))
