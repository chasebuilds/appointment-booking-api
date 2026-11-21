from datetime import date, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import col, select

from app.booking import BookingError, cancel_booking, create_booking
from app.deps import CurrentUser, OwnerOnly, SessionDep, SettingsDep
from app.models import Booking, Reminder, Service, Staff, StaffService, utcnow
from app.schemas import BookingCreated, BookingIn, BookingOut, ReminderOut, SlotOut
from app.slots import free_slots

router = APIRouter(tags=["bookings"])

MAX_RANGE_DAYS = 31


@router.get("/slots", response_model=list[SlotOut])
def list_slots(
    session: SessionDep,
    settings: SettingsDep,
    service_id: int,
    date_from: date,
    date_to: date,
    staff_id: int | None = None,
) -> list[SlotOut]:
    """Open slots in UTC (plus staff-local time). Dates are staff-local calendar days."""
    if date_to < date_from or (date_to - date_from).days >= MAX_RANGE_DAYS:
        raise HTTPException(422, f"date range must be 1 to {MAX_RANGE_DAYS} days")
    service = session.get(Service, service_id)
    if service is None or not service.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    query = (
        select(Staff)
        .join(StaffService, col(StaffService.staff_id) == col(Staff.id))
        .where(StaffService.service_id == service_id, Staff.active)
    )
    if staff_id is not None:
        query = query.where(Staff.id == staff_id)
    duration = timedelta(minutes=service.duration_minutes)
    step = timedelta(minutes=settings.slot_step_minutes)
    earliest = utcnow() + timedelta(minutes=settings.min_notice_minutes)
    out: list[SlotOut] = []
    for staff in session.exec(query.order_by(col(Staff.id))):
        assert staff.id is not None
        tz = ZoneInfo(staff.timezone)
        day = date_from
        while day <= date_to:
            for start in free_slots(session, staff.id, tz, day, duration, step, earliest):
                out.append(
                    SlotOut(
                        staff_id=staff.id,
                        start=start,
                        end=start + duration,
                        local_start=start.astimezone(tz).isoformat(),
                    )
                )
            day += timedelta(days=1)
    out.sort(key=lambda s: (s.start, s.staff_id))
    return out


@router.post("/bookings", response_model=BookingCreated, status_code=status.HTTP_201_CREATED)
def make_booking(body: BookingIn, session: SessionDep, settings: SettingsDep) -> Booking:
    try:
        return create_booking(
            session,
            settings,
            service_id=body.service_id,
            staff_id=body.staff_id,
            start=body.start,
            customer_name=body.customer_name,
            customer_email=body.customer_email,
        )
    except BookingError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/bookings", response_model=list[BookingOut])
def list_bookings(
    session: SessionDep,
    user: CurrentUser,
    status_filter: Annotated[
        Literal["confirmed", "cancelled"] | None, Query(alias="status")
    ] = None,
) -> list[Booking]:
    query = select(Booking).order_by(col(Booking.start_utc))
    if user.role == "staff":
        query = query.where(Booking.staff_id == user.staff_id)
    if status_filter:
        query = query.where(Booking.status == status_filter)
    return list(session.exec(query))


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
def cancel(booking_id: int, session: SessionDep, token: str | None = None) -> Booking:
    """Customers cancel with the token from booking creation; see /bookings/{id}/staff-cancel."""
    booking = session.get(Booking, booking_id)
    if booking is None or token is None or token != booking.cancel_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    try:
        return cancel_booking(session, booking)
    except BookingError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/bookings/{booking_id}/staff-cancel", response_model=BookingOut)
def staff_cancel(booking_id: int, session: SessionDep, user: CurrentUser) -> Booking:
    booking = session.get(Booking, booking_id)
    if booking is None or (user.role == "staff" and booking.staff_id != user.staff_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    try:
        return cancel_booking(session, booking)
    except BookingError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/reminders", response_model=list[ReminderOut])
def list_reminders(
    session: SessionDep,
    _: OwnerOnly,
    status_filter: Annotated[
        Literal["queued", "cancelled", "sent"] | None, Query(alias="status")
    ] = None,
) -> list[Reminder]:
    query = select(Reminder).order_by(col(Reminder.send_at))
    if status_filter:
        query = query.where(Reminder.status == status_filter)
    return list(session.exec(query))
