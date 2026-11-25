"""Glue for the browser demo: seeds an in-memory SQLite database and calls the repo's own
slot engine (app/slots.py) and booking function (app/booking.py). No FastAPI, auth or HTTP:
this mirrors what GET /slots and POST /bookings do inside their route handlers.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.booking import BookingError, create_booking
from app.config import Settings
from app.models import Availability, Booking, Service, Staff, StaffService, TimeOff, utcnow
from app.slots import free_slots

SETTINGS = Settings(database_url="sqlite://", slot_step_minutes=15, min_notice_minutes=60)
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


def seed() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        staff = [
            Staff(name="Avery (New York)", timezone="America/New_York"),
            Staff(name="Sam (London)", timezone="Europe/London"),
            Staff(name="Kai (Los Angeles)", timezone="America/Los_Angeles"),
        ]
        services = [
            Service(name="Consultation, 30 min", duration_minutes=30),
            Service(name="Full session, 60 min", duration_minutes=60),
        ]
        s.add_all(staff + services)
        s.flush()
        for st in staff:
            for sv in services:
                s.add(StaffService(staff_id=st.id, service_id=sv.id))
            for wd in range(5):  # Monday to Friday, with a lunch break
                s.add(
                    Availability(staff_id=st.id, weekday=wd, start_time=time(9), end_time=time(12))
                )
                s.add(
                    Availability(staff_id=st.id, weekday=wd, start_time=time(13), end_time=time(17))
                )
        # Avery is out for an afternoon on the next weekday, to show time off blocking slots.
        day = next_weekday(utcnow().date())
        tz = ZoneInfo("America/New_York")
        s.add(
            TimeOff(
                staff_id=staff[0].id,
                start_utc=datetime.combine(day, time(14), tzinfo=tz),
                end_utc=datetime.combine(day, time(16), tzinfo=tz),
                reason="Dentist",
            )
        )
        s.commit()


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() > 4:
        d += timedelta(days=1)
    return d


def catalog() -> dict[str, object]:
    with Session(engine) as s:
        return {
            "staff": [
                {"id": x.id, "name": x.name, "tz": x.timezone} for x in s.exec(select(Staff))
            ],
            "services": [
                {"id": x.id, "name": x.name, "minutes": x.duration_minutes}
                for x in s.exec(select(Service))
            ],
            "first_day": next_weekday(utcnow().date()).isoformat(),
        }


def slots(staff_id: int, service_id: int, day: str, viewer_tz: str) -> list[dict[str, str]]:
    """Same steps as GET /slots for one staff member and one staff-local day."""
    with Session(engine) as s:
        staff = s.get(Staff, staff_id)
        service = s.get(Service, service_id)
        assert staff is not None and service is not None
        tz = ZoneInfo(staff.timezone)
        duration = timedelta(minutes=service.duration_minutes)
        step = timedelta(minutes=SETTINGS.slot_step_minutes)
        earliest = utcnow() + timedelta(minutes=SETTINGS.min_notice_minutes)
        assert staff.id is not None
        found = free_slots(s, staff.id, tz, date.fromisoformat(day), duration, step, earliest)
        viewer = ZoneInfo(viewer_tz)
        return [
            {
                "utc": start.isoformat(),
                "staff_local": start.astimezone(tz).strftime("%a %H:%M %Z"),
                "viewer_local": start.astimezone(viewer).strftime("%a %b %d, %H:%M %Z"),
            }
            for start in found
        ]


def book(
    staff_id: int, service_id: int, start_utc: str, name: str, email: str
) -> dict[str, object]:
    """Same call as POST /bookings. Returns the booking, or the status and detail it would send."""
    with Session(engine) as s:
        try:
            b = create_booking(
                s,
                SETTINGS,
                service_id=service_id,
                staff_id=staff_id,
                start=datetime.fromisoformat(start_utc),
                customer_name=name,
                customer_email=email,
            )
        except BookingError as e:
            return {"ok": False, "status": e.status_code, "detail": e.detail}
        return {"ok": True, "status": 201, "id": b.id, "start_utc": b.start_utc.isoformat()}


def bookings() -> list[dict[str, object]]:
    with Session(engine) as s:
        rows = s.exec(
            select(Booking, Staff)
            .where(Booking.staff_id == Staff.id)
            .order_by(col(Booking.start_utc))
        )
        return [
            {
                "id": b.id,
                "staff": st.name,
                "customer": b.customer_name,
                "start_staff_local": b.start_utc.astimezone(ZoneInfo(st.timezone)).strftime(
                    "%a %b %d, %H:%M %Z"
                ),
                "status": b.status,
            }
            for b, st in rows
        ]
