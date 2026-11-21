from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import utcnow

OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "correct horse battery"
NY = ZoneInfo("America/New_York")


def next_weekday(weekday: int, min_days_ahead: int = 8) -> date:
    """First date with the given weekday (Monday = 0) at least N days ahead in New York."""
    day = datetime.now(NY).date() + timedelta(days=min_days_ahead)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        secret_key="test-secret-test-secret-test-secret",
        auto_create_tables=True,
        owner_email=OWNER_EMAIL,
        owner_password=OWNER_PASSWORD,
        login_rate_limit=5,
        min_notice_minutes=60,
        slot_step_minutes=30,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def owner(client: TestClient) -> dict[str, str]:
    return login(client, OWNER_EMAIL, OWNER_PASSWORD)


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str]) -> dict[str, int]:
    """One 30-minute service and one staff member free Mondays 09:00-12:00 New York time."""
    svc = client.post(
        "/services",
        json={"name": "Haircut", "duration_minutes": 30, "price_cents": 3500},
        headers=owner,
    ).json()
    staff = client.post("/staff", json={"name": "Alice"}, headers=owner).json()
    r = client.put(
        f"/staff/{staff['id']}/services", json={"service_ids": [svc["id"]]}, headers=owner
    )
    assert r.status_code == 200
    r = client.put(
        f"/staff/{staff['id']}/availability",
        json={"windows": [{"weekday": 0, "start_time": "09:00", "end_time": "12:00"}]},
        headers=owner,
    )
    assert r.status_code == 200, r.text
    return {"service_id": svc["id"], "staff_id": staff["id"]}


def booking_body(shop: dict[str, int], start: str, name: str = "Pat Customer") -> dict[str, object]:
    return {
        "service_id": shop["service_id"],
        "staff_id": shop["staff_id"],
        "start": start,
        "customer_name": name,
        "customer_email": "pat@example.com",
    }


@pytest.fixture
def monday() -> date:
    return next_weekday(0)


def local_iso(day: date, hh: int, mm: int = 0) -> str:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=NY).isoformat()


__all__ = ["utcnow"]
