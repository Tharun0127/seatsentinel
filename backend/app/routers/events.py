"""Events and their seat maps."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func, select

from app import holds
from app.auth import optional_user_id
from app.models import SEAT_BOOKED, Event, Seat
from app.schemas import EventOut, SeatOut

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventOut])
async def list_events(request: Request):
    booked = func.count(case((Seat.status == SEAT_BOOKED, 1)))
    query = (
        select(Event, func.count(Seat.id), booked)
        .join(Seat, Seat.event_id == Event.id)
        .group_by(Event.id)
        .order_by(Event.starts_at)
    )
    async with request.app.state.session_factory() as session:
        rows = (await session.execute(query)).all()
    return [
        EventOut(id=e.id, name=e.name, venue=e.venue, starts_at=e.starts_at, seats_total=total, seats_booked=b)
        for e, total, b in rows
    ]


@router.get("/{event_id}/seats", response_model=list[SeatOut])
async def seat_map(event_id: int, request: Request, user_id: int | None = Depends(optional_user_id)):
    """Seat states merged from two sources: booked (Postgres) and held (Redis).

    The frontend polls this every 2 seconds.
    """
    async with request.app.state.session_factory() as session:
        seats = (await session.scalars(
            select(Seat).where(Seat.event_id == event_id).order_by(Seat.row_label, Seat.number)
        )).all()
    if not seats:
        raise HTTPException(404, "event not found")
    current_holds = await holds.current(request.app.state.redis, [s.id for s in seats])
    return [seat_out(seat, current_holds.get(seat.id), user_id) for seat in seats]


def seat_out(seat: Seat, hold: holds.Hold | None, user_id: int | None) -> SeatOut:
    base = {"id": seat.id, "row_label": seat.row_label, "number": seat.number, "price": seat.price}
    if seat.status == SEAT_BOOKED:
        # Booked wins over held: a hold can briefly outlive the booking.
        return SeatOut(**base, state="booked", mine=seat.booked_by == user_id)
    if hold is not None:
        mine = hold.user_id == user_id
        return SeatOut(**base, state="held", mine=mine, hold_expires_in_ms=hold.expires_in_ms if mine else None)
    return SeatOut(**base, state="free", mine=False)
