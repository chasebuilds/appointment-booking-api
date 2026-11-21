"""Slot generation across DST changes and unusual offsets (pure DB, no HTTP)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session, SQLModel

from app.db import make_engine
from app.models import Availability, Booking, Service, Staff, TimeOff
from app.slots import base_slots, free_slots

HALF_HOUR = timedelta(minutes=30)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 'slots.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Staff(id=1, name="Alice", timezone="America/New_York"))
        s.add(Service(id=1, name="Cut", duration_minutes=30))
        s.commit()
        yield s


def add_window(session: Session, weekday: int, start: time, end: time) -> None:
    session.add(Availability(staff_id=1, weekday=weekday, start_time=start, end_time=end))
    session.commit()


def slots(session: Session, tz: str, day: date) -> list[datetime]:
    return base_slots(session, 1, ZoneInfo(tz), day, HALF_HOUR, HALF_HOUR)


def test_normal_day_has_16_slots(session: Session) -> None:
    add_window(session, 0, time(9), time(17))
    assert len(slots(session, "America/New_York", date(2026, 3, 2))) == 16


def test_utc_offset_changes_across_spring_forward(session: Session) -> None:
    add_window(session, 0, time(9), time(17))
    before = slots(session, "America/New_York", date(2026, 3, 2))[0]  # EST
    after = slots(session, "America/New_York", date(2026, 3, 9))[0]  # EDT
    assert before == datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    assert after == datetime(2026, 3, 9, 13, 0, tzinfo=UTC)


def test_spring_forward_overnight_window_is_one_hour_shorter(session: Session) -> None:
    add_window(session, 6, time(0), time(8))  # Sunday 2026-03-08
    assert len(slots(session, "America/New_York", date(2026, 3, 8))) == 14
    assert len(slots(session, "America/New_York", date(2026, 3, 15))) == 16


def test_fall_back_overnight_window_is_one_hour_longer(session: Session) -> None:
    add_window(session, 6, time(0), time(8))  # Sunday 2026-11-01
    assert len(slots(session, "America/New_York", date(2026, 11, 1))) == 18


def test_window_inside_the_spring_forward_gap_does_not_crash(session: Session) -> None:
    add_window(session, 6, time(2, 30), time(4, 0))  # 02:30 does not exist on 2026-03-08
    result = slots(session, "America/New_York", date(2026, 3, 8))
    assert result == [datetime(2026, 3, 8, 7, 30, tzinfo=UTC)]  # 03:30 EDT, fold=0 rule


def test_slots_are_strictly_increasing_and_utc(session: Session) -> None:
    add_window(session, 6, time(0), time(8))
    result = slots(session, "America/New_York", date(2026, 11, 1))
    assert result == sorted(set(result))
    assert all(s.tzinfo is UTC or s.utcoffset() == timedelta(0) for s in result)


def test_half_hour_offset_zone(session: Session) -> None:
    add_window(session, 0, time(9), time(10))
    result = slots(session, "Asia/Kolkata", date(2026, 3, 2))
    assert result[0] == datetime(2026, 3, 2, 3, 30, tzinfo=UTC)  # 09:00 at +05:30


def test_zone_across_date_line_uses_local_calendar_day(session: Session) -> None:
    add_window(session, 0, time(9), time(10))  # local Monday
    result = slots(session, "Pacific/Auckland", date(2026, 3, 2))
    assert result[0] == datetime(2026, 3, 1, 20, 0, tzinfo=UTC)  # Sunday in UTC


def test_duration_longer_than_window_gives_nothing(session: Session) -> None:
    add_window(session, 0, time(9), time(9, 45))
    ny = ZoneInfo("America/New_York")
    assert base_slots(session, 1, ny, date(2026, 3, 2), timedelta(hours=1), HALF_HOUR) == []


def test_step_smaller_than_duration_overlaps_by_design(session: Session) -> None:
    add_window(session, 0, time(9), time(10))
    ny = ZoneInfo("America/New_York")
    result = base_slots(session, 1, ny, date(2026, 3, 2), HALF_HOUR, timedelta(minutes=15))
    assert len(result) == 3  # 09:00, 09:15, 09:30


def test_booking_and_time_off_remove_slots(session: Session) -> None:
    add_window(session, 0, time(9), time(11))
    ny = ZoneInfo("America/New_York")
    day = date(2026, 3, 2)
    all_slots = slots(session, "America/New_York", day)
    assert len(all_slots) == 4
    session.add(
        Booking(
            service_id=1,
            staff_id=1,
            customer_name="x",
            customer_email="x@example.com",
            start_utc=all_slots[0],
            end_utc=all_slots[0] + HALF_HOUR,
            cancel_token="t",
        )
    )
    session.add(TimeOff(staff_id=1, start_utc=all_slots[2], end_utc=all_slots[2] + HALF_HOUR))
    session.commit()
    earliest = datetime(2026, 1, 1, tzinfo=UTC)
    free = free_slots(session, 1, ny, day, HALF_HOUR, HALF_HOUR, earliest)
    assert free == [all_slots[1], all_slots[3]]


def test_cancelled_booking_does_not_block(session: Session) -> None:
    add_window(session, 0, time(9), time(10))
    ny = ZoneInfo("America/New_York")
    day = date(2026, 3, 2)
    first = slots(session, "America/New_York", day)[0]
    session.add(
        Booking(
            service_id=1,
            staff_id=1,
            customer_name="x",
            customer_email="x@example.com",
            start_utc=first,
            end_utc=first + HALF_HOUR,
            status="cancelled",
            cancel_token="t",
        )
    )
    session.commit()
    earliest = datetime(2026, 1, 1, tzinfo=UTC)
    assert len(free_slots(session, 1, ny, day, HALF_HOUR, HALF_HOUR, earliest)) == 2


def test_earliest_cutoff_hides_past_slots(session: Session) -> None:
    add_window(session, 0, time(9), time(11))
    ny = ZoneInfo("America/New_York")
    day = date(2026, 3, 2)
    cutoff = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)  # 10:00 EST
    free = free_slots(session, 1, ny, day, HALF_HOUR, HALF_HOUR, cutoff)
    assert free[0] == cutoff and len(free) == 2
