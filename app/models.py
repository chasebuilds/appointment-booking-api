from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import DateTime, Index, TypeDecorator, text
from sqlmodel import Field, SQLModel


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC, always hand back timezone-aware UTC (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Staff(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    timezone: str
    active: bool = True


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    role: str  # "owner" or "staff"
    staff_id: int | None = Field(default=None, foreign_key="staff.id")
    active: bool = True


class Service(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    duration_minutes: int
    price_cents: int = 0
    active: bool = True


class StaffService(SQLModel, table=True):
    staff_id: int = Field(foreign_key="staff.id", primary_key=True)
    service_id: int = Field(foreign_key="service.id", primary_key=True)


class Availability(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    staff_id: int = Field(foreign_key="staff.id", index=True)
    weekday: int  # Monday = 0
    start_time: time  # local wall clock in the staff member's timezone
    end_time: time


class TimeOff(SQLModel, table=True):
    __tablename__ = "time_off"

    id: int | None = Field(default=None, primary_key=True)
    staff_id: int = Field(foreign_key="staff.id", index=True)
    start_utc: datetime = Field(sa_type=UTCDateTime)
    end_utc: datetime = Field(sa_type=UTCDateTime)
    reason: str = ""


class Booking(SQLModel, table=True):
    __table_args__ = (
        Index(
            "uq_booking_staff_start_confirmed",
            "staff_id",
            "start_utc",
            unique=True,
            sqlite_where=text("status = 'confirmed'"),
            postgresql_where=text("status = 'confirmed'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    service_id: int = Field(foreign_key="service.id")
    staff_id: int = Field(foreign_key="staff.id", index=True)
    customer_name: str
    customer_email: str
    start_utc: datetime = Field(sa_type=UTCDateTime)
    end_utc: datetime = Field(sa_type=UTCDateTime)
    status: str = "confirmed"  # confirmed | cancelled
    cancel_token: str
    created_at: datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class Reminder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    booking_id: int = Field(foreign_key="booking.id", index=True)
    send_at: datetime = Field(sa_type=UTCDateTime)
    kind: str  # "24h" | "1h"
    status: str = "queued"  # queued | cancelled | sent
