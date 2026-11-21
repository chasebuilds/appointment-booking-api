"""Many clients race for the same slot. Exactly one must win."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.booking import BookingError, create_booking
from app.config import Settings
from app.db import make_engine
from app.models import Booking

from .conftest import booking_body, local_iso

THREADS = 24


def _try_book(settings: Settings, shop: dict[str, int], start: str, i: int) -> str:
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        try:
            create_booking(
                session,
                settings,
                service_id=shop["service_id"],
                staff_id=shop["staff_id"],
                start=datetime.fromisoformat(start),
                customer_name=f"c{i}",
                customer_email=f"c{i}@example.com",
            )
        except BookingError as exc:
            return str(exc.status_code)
    return "ok"


def test_same_slot_service_level(
    settings: Settings, client: TestClient, shop: dict[str, int], monday: date
) -> None:
    start = local_iso(monday, 9)
    with ThreadPoolExecutor(THREADS) as pool:
        results = list(pool.map(lambda i: _try_book(settings, shop, start, i), range(THREADS)))
    assert results.count("ok") == 1
    assert results.count("409") == THREADS - 1


def test_overlapping_starts_cannot_both_win(
    settings: Settings, client: TestClient, shop: dict[str, int], monday: date
) -> None:
    """09:00 and 09:15 overlap (30 min each). The unique index alone would allow both."""
    settings_step = settings.model_copy(update={"slot_step_minutes": 15})
    starts = [local_iso(monday, 9, 0), local_iso(monday, 9, 15)] * 8
    # slot_step 15 lets 09:15 be a valid start in this test
    with ThreadPoolExecutor(len(starts)) as pool:
        results = list(
            pool.map(lambda a: _try_book(settings_step, shop, a[1], a[0]), enumerate(starts))
        )
    assert results.count("ok") == 1
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        assert len(session.exec(select(Booking)).all()) == 1


def test_same_slot_over_http(client: TestClient, shop: dict[str, int], monday: date) -> None:
    body = booking_body(shop, local_iso(monday, 10))
    with ThreadPoolExecutor(THREADS) as pool:
        codes = list(
            pool.map(lambda _: client.post("/bookings", json=body).status_code, range(THREADS))
        )
    assert codes.count(201) == 1
    assert codes.count(409) == THREADS - 1


def test_different_slots_all_succeed_in_parallel(
    client: TestClient, shop: dict[str, int], monday: date
) -> None:
    starts = [local_iso(monday, 9, m) for m in (0, 30)] + [
        local_iso(monday, 10, m) for m in (0, 30)
    ]
    with ThreadPoolExecutor(len(starts)) as pool:
        codes = list(
            pool.map(
                lambda s: client.post("/bookings", json=booking_body(shop, s)).status_code, starts
            )
        )
    assert codes == [201] * len(starts)


def test_unique_index_is_a_backstop_when_locks_are_bypassed(
    settings: Settings, client: TestClient, shop: dict[str, int], monday: date
) -> None:
    r = client.post("/bookings", json=booking_body(shop, local_iso(monday, 11)))
    assert r.status_code == 201
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        first = session.exec(select(Booking)).one()
        dupe = Booking(
            service_id=first.service_id,
            staff_id=first.staff_id,
            customer_name="dupe",
            customer_email="d@example.com",
            start_utc=first.start_utc,
            end_utc=first.start_utc + timedelta(minutes=30),
            cancel_token="other",
        )
        session.add(dupe)
        with pytest.raises(IntegrityError):
            session.commit()
