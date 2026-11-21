"""Timezone-correct slot generation and conflict checks.

Availability is stored as local wall-clock windows in the staff member's timezone.
Each window is converted to UTC for its own date, then walked in real elapsed time,
so DST days produce the right number of slots. A wall time that does not exist
(spring-forward gap) resolves with fold=0 per PEP 495; an ambiguous one (fall back)
resolves to its first occurrence.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models import Availability, Booking, TimeOff


def window_utc(tz: ZoneInfo, day: date, availability: Availability) -> tuple[datetime, datetime]:
    start = datetime.combine(day, availability.start_time, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day, availability.end_time, tzinfo=tz).astimezone(UTC)
    return start, end


def base_slots(
    session: Session,
    staff_id: int,
    tz: ZoneInfo,
    day: date,
    duration: timedelta,
    step: timedelta,
) -> list[datetime]:
    """Every slot start the weekly schedule allows, ignoring bookings and time off."""
    rows = session.exec(
        select(Availability)
        .where(Availability.staff_id == staff_id, Availability.weekday == day.weekday())
        .order_by(col(Availability.start_time))
    ).all()
    slots: list[datetime] = []
    for row in rows:
        start, end = window_utc(tz, day, row)
        cur = start
        while cur + duration <= end:
            slots.append(cur)
            cur += step
    return slots


def is_free(session: Session, staff_id: int, start: datetime, end: datetime) -> bool:
    booked = session.exec(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.staff_id == staff_id,
            Booking.status == "confirmed",
            col(Booking.start_utc) < end,
            col(Booking.end_utc) > start,
        )
    ).one()
    off = session.exec(
        select(func.count())
        .select_from(TimeOff)
        .where(
            TimeOff.staff_id == staff_id,
            col(TimeOff.start_utc) < end,
            col(TimeOff.end_utc) > start,
        )
    ).one()
    return booked == 0 and off == 0


def free_slots(
    session: Session,
    staff_id: int,
    tz: ZoneInfo,
    day: date,
    duration: timedelta,
    step: timedelta,
    earliest: datetime,
) -> list[datetime]:
    return [
        start
        for start in base_slots(session, staff_id, tz, day, duration, step)
        if start >= earliest and is_free(session, staff_id, start, start + duration)
    ]
