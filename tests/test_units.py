from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.booking import cancel_booking
from app.config import Settings
from app.db import make_engine
from app.models import Booking, UTCDateTime
from app.ratelimit import SlidingWindowLimiter
from app.security import create_token, decode_token, hash_password, verify_password


def test_limiter_window_slides_with_clock() -> None:
    now = [0.0]
    lim = SlidingWindowLimiter(2, 10, clock=lambda: now[0])
    assert lim.check("a") is None
    assert lim.check("a") is None
    wait = lim.check("a")
    assert wait is not None and 9 < wait <= 10
    assert lim.check("b") is None  # other keys unaffected
    now[0] = 10.5
    assert lim.check("a") is None


def test_limiter_blocks_when_any_key_is_over() -> None:
    lim = SlidingWindowLimiter(1, 60, clock=lambda: 0.0)
    assert lim.check("ip:1", "email:x") is None
    assert lim.check("ip:2", "email:x") is not None  # email key exhausted


def test_password_hash_and_token_round_trip() -> None:
    h = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter2", h) and not verify_password("nope", h)
    assert not verify_password("x", "not-a-bcrypt-hash")
    token = create_token(7, "owner", "s" * 32, 5)
    assert decode_token(token, "s" * 32) is not None
    assert decode_token(token, "t" * 32) is None
    expired = create_token(7, "owner", "s" * 32, -1)
    assert decode_token(expired, "s" * 32) is None


def test_utc_datetime_type_rejects_naive_and_localises() -> None:
    t = UTCDateTime()
    assert t.process_bind_param(None, None) is None
    assert t.process_result_value(None, None) is None
    assert t.process_result_value(datetime(2026, 1, 1, 5), None) == datetime(
        2026, 1, 1, 5, tzinfo=UTC
    )
    try:
        t.process_bind_param(datetime(2026, 1, 1), None)
    except ValueError:
        pass
    else:
        raise AssertionError("naive datetime accepted")


def test_cancel_past_booking_refused(settings: Settings) -> None:
    from sqlmodel import Session, SQLModel

    from app.booking import BookingError

    engine = make_engine(settings.database_url)
    SQLModel.metadata.create_all(engine)
    past = datetime.now(UTC) - timedelta(hours=1)
    with Session(engine) as s:
        b = Booking(
            service_id=1,
            staff_id=1,
            customer_name="x",
            customer_email="x@example.com",
            start_utc=past,
            end_utc=past + timedelta(minutes=30),
            cancel_token="t",
        )
        try:
            cancel_booking(s, b)
        except BookingError as exc:
            assert exc.status_code == 409
        else:
            raise AssertionError("past booking cancelled")


def test_alembic_upgrade_and_downgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    tables = set(inspect(make_engine(url)).get_table_names())
    assert {"service", "staff", "user", "availability", "time_off", "booking", "reminder"} <= tables
    index_names = {i["name"] for i in inspect(make_engine(url)).get_indexes("booking")}
    assert "uq_booking_staff_start_confirmed" in index_names
    command.downgrade(cfg, "base")
    assert "booking" not in inspect(make_engine(url)).get_table_names()


def test_seed_owner_is_idempotent_and_skipped_without_env(settings: Settings) -> None:
    from sqlmodel import Session, select

    from app.main import create_app, seed_owner
    from app.models import User

    app = create_app(settings.model_copy(update={"owner_email": None}))
    seed_owner(app)  # no env: no-op, tables may not even exist
    app = create_app(settings)
    from fastapi.testclient import TestClient

    with TestClient(app):
        seed_owner(app)
        with Session(app.state.engine) as s:
            assert len(s.exec(select(User)).all()) == 1
