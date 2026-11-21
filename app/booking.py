"""Booking and cancellation. The only place that writes bookings.

Double-booking protection has three layers:
1. A per-staff lock inside this process (covers SQLite and single-node deployments).
2. SELECT ... FOR UPDATE on the staff row (covers several workers on Postgres).
3. A partial unique index on (staff_id, start_utc) for confirmed bookings.
"""

import secrets
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.models import Booking, Reminder, Service, Staff, StaffService, utcnow
from app.slots import base_slots, is_free

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()

REMINDER_OFFSETS = {"24h": timedelta(hours=24), "1h": timedelta(hours=1)}


class BookingError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _lock_for(staff_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(staff_id, threading.Lock())


def create_booking(
    session: Session,
    settings: Settings,
    *,
    service_id: int,
    staff_id: int,
    start: datetime,
    customer_name: str,
    customer_email: str,
    now: datetime | None = None,
) -> Booking:
    now = now or utcnow()
    start = start.astimezone(utcnow().tzinfo)

    with _lock_for(staff_id):
        staff = session.exec(select(Staff).where(Staff.id == staff_id).with_for_update()).first()
        service = session.get(Service, service_id)
        if staff is None or not staff.active:
            raise BookingError(404, "Staff member not found")
        if service is None or not service.active:
            raise BookingError(404, "Service not found")
        offered = session.get(StaffService, (staff_id, service_id))
        if offered is None:
            raise BookingError(422, "This staff member does not offer that service")

        duration = timedelta(minutes=service.duration_minutes)
        if start < now + timedelta(minutes=settings.min_notice_minutes):
            raise BookingError(422, "Start is too soon or in the past")
        if start > now + timedelta(days=settings.max_days_ahead):
            raise BookingError(422, "Start is too far ahead")

        tz = ZoneInfo(staff.timezone)
        day = start.astimezone(tz).date()
        step = timedelta(minutes=settings.slot_step_minutes)
        if start not in base_slots(session, staff_id, tz, day, duration, step):
            raise BookingError(422, "Start is not an available slot for this staff member")
        end = start + duration
        if not is_free(session, staff_id, start, end):
            raise BookingError(409, "That slot is no longer available")

        booking = Booking(
            service_id=service_id,
            staff_id=staff_id,
            customer_name=customer_name,
            customer_email=customer_email,
            start_utc=start,
            end_utc=end,
            cancel_token=secrets.token_urlsafe(24),
        )
        session.add(booking)
        try:
            session.flush()
            assert booking.id is not None
            for kind, offset in REMINDER_OFFSETS.items():
                send_at = start - offset
                if send_at > now:
                    session.add(Reminder(booking_id=booking.id, send_at=send_at, kind=kind))
            session.commit()
        except IntegrityError:
            session.rollback()
            raise BookingError(409, "That slot is no longer available") from None
        session.refresh(booking)
        return booking


def cancel_booking(session: Session, booking: Booking, now: datetime | None = None) -> Booking:
    now = now or utcnow()
    if booking.status == "cancelled":
        raise BookingError(409, "Booking is already cancelled")
    if booking.start_utc <= now:
        raise BookingError(409, "Past bookings cannot be cancelled")
    booking.status = "cancelled"
    session.add(booking)
    for reminder in session.exec(
        select(Reminder).where(Reminder.booking_id == booking.id, Reminder.status == "queued")
    ):
        reminder.status = "cancelled"
        session.add(reminder)
    session.commit()
    session.refresh(booking)
    return booking
