"""Events and their seat maps."""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import case, func, select

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
async def seat_map(event_id: int, request: Request):
    async with request.app.state.session_factory() as session:
        seats = (await session.scalars(
            select(Seat).where(Seat.event_id == event_id).order_by(Seat.row_label, Seat.number)
        )).all()
    if not seats:
        raise HTTPException(404, "event not found")
    return [
        SeatOut(id=s.id, row_label=s.row_label, number=s.number, price=s.price,
                state="booked" if s.status == SEAT_BOOKED else "free", mine=False)
        for s in seats
    ]
