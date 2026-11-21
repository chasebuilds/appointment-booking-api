from datetime import datetime, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)

Name = Annotated[str, Field(min_length=1, max_length=100)]


def _valid_tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"unknown timezone: {value}") from exc
    return value


TimezoneName = Annotated[str, AfterValidator(_valid_tz)]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a UTC offset, e.g. 2026-03-09T10:00:00-04:00")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_aware)]


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    role: Literal["owner", "staff"] = "staff"
    staff_id: int | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    role: str
    staff_id: int | None


class ServiceIn(BaseModel):
    name: Name
    duration_minutes: int = Field(ge=5, le=480)
    price_cents: int = Field(default=0, ge=0, le=10_000_000)


class ServiceUpdate(BaseModel):
    name: Name | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    price_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    active: bool | None = None


class ServiceOut(ServiceIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    active: bool


class StaffIn(BaseModel):
    name: Name
    timezone: TimezoneName | None = None


class StaffUpdate(BaseModel):
    name: Name | None = None
    timezone: TimezoneName | None = None
    active: bool | None = None


class StaffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    timezone: str
    active: bool


class StaffServicesIn(BaseModel):
    service_ids: list[int]


class AvailabilityWindow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    weekday: int = Field(ge=0, le=6, description="Monday = 0")
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def _ordered(self) -> "AvailabilityWindow":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class AvailabilityIn(BaseModel):
    windows: list[AvailabilityWindow] = Field(max_length=50)

    @model_validator(mode="after")
    def _no_overlap(self) -> "AvailabilityIn":
        ordered = sorted(self.windows, key=lambda w: (w.weekday, w.start_time))
        for a, b in zip(ordered, ordered[1:], strict=False):
            if a.weekday == b.weekday and b.start_time < a.end_time:
                raise ValueError("availability windows overlap on the same weekday")
        return self


class TimeOffIn(BaseModel):
    start: AwareDatetime
    end: AwareDatetime
    reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _ordered(self) -> "TimeOffIn":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class TimeOffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    staff_id: int
    start_utc: datetime
    end_utc: datetime
    reason: str


class SlotOut(BaseModel):
    staff_id: int
    start: datetime = Field(description="UTC")
    end: datetime = Field(description="UTC")
    local_start: str = Field(description="Staff-local ISO time with offset")


class BookingIn(BaseModel):
    service_id: int
    staff_id: int
    start: AwareDatetime
    customer_name: Name
    customer_email: EmailStr


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    service_id: int
    staff_id: int
    customer_name: str
    customer_email: str
    start_utc: datetime
    end_utc: datetime
    status: str


class BookingCreated(BookingOut):
    cancel_token: str


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    booking_id: int
    send_at: datetime
    kind: str
    status: str
