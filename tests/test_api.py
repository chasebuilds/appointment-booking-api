from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from .conftest import NY, OWNER_EMAIL, OWNER_PASSWORD, booking_body, local_iso, login, next_weekday


def make_staff_login(
    client: TestClient, owner: dict[str, str], staff_id: int, email: str
) -> dict[str, str]:
    r = client.post(
        "/auth/users",
        json={
            "email": email,
            "password": "staff password 123",
            "role": "staff",
            "staff_id": staff_id,
        },
        headers=owner,
    )
    assert r.status_code == 201, r.text
    return login(client, email, "staff password 123")


# ---- health, docs -------------------------------------------------------


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_openapi_lists_core_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/slots", "/bookings", "/auth/login", "/staff/{staff_id}/availability"):
        assert p in paths


# ---- auth ---------------------------------------------------------------


def test_login_bad_password_and_unknown_user(client: TestClient) -> None:
    for email, pw in ((OWNER_EMAIL, "wrong"), ("nobody@example.com", "whatever")):
        r = client.post("/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 401


def test_login_email_is_case_insensitive(client: TestClient) -> None:
    r = client.post("/auth/login", json={"email": OWNER_EMAIL.upper(), "password": OWNER_PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "owner"


def test_login_rate_limited_then_retry_after(client: TestClient) -> None:
    codes = [
        client.post("/auth/login", json={"email": "x@example.com", "password": "bad"}).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [401] * 5
    assert codes[5:] == [429, 429]
    r = client.post("/auth/login", json={"email": "x@example.com", "password": "bad"})
    assert int(r.headers["Retry-After"]) >= 1


def test_protected_routes_need_a_valid_token(client: TestClient) -> None:
    assert client.post("/services", json={"name": "x", "duration_minutes": 30}).status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/bookings", headers=bad).status_code == 401


def test_deactivated_user_token_stops_working(client: TestClient, owner: dict[str, str]) -> None:
    from sqlmodel import Session, select

    from app.models import User

    with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
        u = s.exec(select(User)).one()
        u.active = False
        s.add(u)
        s.commit()
    assert client.get("/bookings", headers=owner).status_code == 401


def test_create_user_rules(client: TestClient, owner: dict[str, str], shop: dict[str, int]) -> None:
    base = {"email": "a@example.com", "password": "long enough pw 1"}
    assert (
        client.post("/auth/users", json={**base, "role": "staff"}, headers=owner).status_code == 422
    )
    assert (
        client.post(
            "/auth/users", json={**base, "role": "owner", "staff_id": 1}, headers=owner
        ).status_code
        == 422
    )
    assert (
        client.post("/auth/users", json={**base, "password": "short"}, headers=owner).status_code
        == 422
    )
    ok = {**base, "role": "staff", "staff_id": shop["staff_id"]}
    assert client.post("/auth/users", json=ok, headers=owner).status_code == 201
    assert client.post("/auth/users", json=ok, headers=owner).status_code == 409


# ---- catalog ------------------------------------------------------------


def test_services_crud_and_validation(client: TestClient, owner: dict[str, str]) -> None:
    for bad in (
        {"name": "", "duration_minutes": 30},
        {"name": "x", "duration_minutes": 1},
        {"name": "x", "duration_minutes": 30, "price_cents": -1},
    ):
        assert client.post("/services", json=bad, headers=owner).status_code == 422
    sid = client.post(
        "/services", json={"name": "Trim", "duration_minutes": 15}, headers=owner
    ).json()["id"]
    r = client.patch(f"/services/{sid}", json={"price_cents": 1200}, headers=owner)
    assert r.json()["price_cents"] == 1200
    assert client.patch("/services/999", json={"name": "z"}, headers=owner).status_code == 404
    client.patch(f"/services/{sid}", json={"active": False}, headers=owner)
    assert client.get("/services").json() == []


def test_staff_crud_and_timezone_validation(client: TestClient, owner: dict[str, str]) -> None:
    assert (
        client.post(
            "/staff", json={"name": "B", "timezone": "Mars/Base"}, headers=owner
        ).status_code
        == 422
    )
    s = client.post("/staff", json={"name": "B", "timezone": "Europe/London"}, headers=owner).json()
    assert s["timezone"] == "Europe/London"
    r = client.patch(f"/staff/{s['id']}", json={"name": "Bea"}, headers=owner)
    assert r.json()["name"] == "Bea"
    assert client.patch("/staff/99", json={"name": "z"}, headers=owner).status_code == 404
    client.patch(f"/staff/{s['id']}", json={"active": False}, headers=owner)
    assert client.get("/staff").json() == []


def test_staff_services_validation(
    client: TestClient, owner: dict[str, str], shop: dict[str, int]
) -> None:
    url = f"/staff/{shop['staff_id']}/services"
    assert client.put(url, json={"service_ids": [999]}, headers=owner).status_code == 422
    assert (
        client.put("/staff/999/services", json={"service_ids": []}, headers=owner).status_code
        == 404
    )


def test_availability_validation(
    client: TestClient, owner: dict[str, str], shop: dict[str, int]
) -> None:
    url = f"/staff/{shop['staff_id']}/availability"
    backwards = {"windows": [{"weekday": 1, "start_time": "10:00", "end_time": "09:00"}]}
    overlap = {
        "windows": [
            {"weekday": 1, "start_time": "09:00", "end_time": "12:00"},
            {"weekday": 1, "start_time": "11:00", "end_time": "13:00"},
        ]
    }
    bad_day = {"windows": [{"weekday": 7, "start_time": "09:00", "end_time": "10:00"}]}
    for body in (backwards, overlap, bad_day):
        assert client.put(url, json=body, headers=owner).status_code == 422
    assert client.get(url).json() == [
        {"weekday": 0, "start_time": "09:00:00", "end_time": "12:00:00"}
    ]
    assert client.get("/staff/99/availability").status_code == 404


def test_roles_staff_manage_only_themselves(
    client: TestClient, owner: dict[str, str], shop: dict[str, int]
) -> None:
    other = client.post("/staff", json={"name": "Bob"}, headers=owner).json()["id"]
    me = make_staff_login(client, owner, shop["staff_id"], "alice@example.com")
    win = {"windows": [{"weekday": 1, "start_time": "09:00", "end_time": "10:00"}]}
    assert (
        client.put(f"/staff/{shop['staff_id']}/availability", json=win, headers=me).status_code
        == 200
    )
    assert client.put(f"/staff/{other}/availability", json=win, headers=me).status_code == 403
    assert (
        client.post("/services", json={"name": "x", "duration_minutes": 30}, headers=me).status_code
        == 403
    )
    assert client.get("/reminders", headers=me).status_code == 403
    assert client.post("/auth/users", json={}, headers=me).status_code == 403


def test_time_off_crud_and_permissions(
    client: TestClient, owner: dict[str, str], shop: dict[str, int]
) -> None:
    sid = shop["staff_id"]
    body = {
        "start": "2027-01-04T09:00:00-05:00",
        "end": "2027-01-04T12:00:00-05:00",
        "reason": "dentist",
    }
    r = client.post(f"/staff/{sid}/time-off", json=body, headers=owner)
    assert r.status_code == 201 and r.json()["start_utc"].startswith("2027-01-04T14:00")
    tid = r.json()["id"]
    assert len(client.get(f"/staff/{sid}/time-off", headers=owner).json()) == 1
    naive = {"start": "2027-01-04T09:00:00", "end": "2027-01-04T10:00:00"}
    assert client.post(f"/staff/{sid}/time-off", json=naive, headers=owner).status_code == 422
    reversed_ = {"start": body["end"], "end": body["start"]}
    assert client.post(f"/staff/{sid}/time-off", json=reversed_, headers=owner).status_code == 422
    other = client.post("/staff", json={"name": "Bob"}, headers=owner).json()["id"]
    me = make_staff_login(client, owner, other, "bob@example.com")
    assert client.get(f"/staff/{sid}/time-off", headers=me).status_code == 403
    assert client.delete(f"/staff/{sid}/time-off/{tid}", headers=me).status_code == 403
    assert client.delete(f"/staff/{sid}/time-off/999", headers=owner).status_code == 404
    assert client.delete(f"/staff/{sid}/time-off/{tid}", headers=owner).status_code == 204


# ---- slots and bookings -------------------------------------------------


def test_slots_listing_in_utc_and_local(
    client: TestClient, shop: dict[str, int], monday: date
) -> None:
    r = client.get(
        "/slots",
        params={"service_id": shop["service_id"], "date_from": monday, "date_to": monday},
    )
    slots = r.json()
    assert len(slots) == 6  # 09:00-12:00 in 30 minute steps (step 15 would give more)
    first = datetime.fromisoformat(slots[0]["start"])
    assert first == datetime(monday.year, monday.month, monday.day, 9, tzinfo=NY).astimezone(UTC)
    assert slots[0]["local_start"].startswith(f"{monday}T09:00:00")


def test_slots_param_validation(client: TestClient, shop: dict[str, int], monday: date) -> None:
    p = {"service_id": shop["service_id"], "date_from": monday}
    assert (
        client.get("/slots", params={**p, "date_to": monday - timedelta(days=1)}).status_code == 422
    )
    assert (
        client.get("/slots", params={**p, "date_to": monday + timedelta(days=40)}).status_code
        == 422
    )
    assert (
        client.get(
            "/slots", params={"service_id": 99, "date_from": monday, "date_to": monday}
        ).status_code
        == 404
    )
    other = client.get("/slots", params={**p, "date_to": monday, "staff_id": 99}).json()
    assert other == []


def test_book_then_double_book_then_cancel_frees_slot(
    client: TestClient, owner: dict[str, str], shop: dict[str, int], monday: date
) -> None:
    body = booking_body(shop, local_iso(monday, 9))
    r = client.post("/bookings", json=body)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["status"] == "confirmed" and created["cancel_token"]

    assert client.post("/bookings", json=body).status_code == 409
    overlap = booking_body(shop, local_iso(monday, 9, 15))
    assert client.post("/bookings", json=overlap).status_code == 422  # not on the 30 minute grid

    params = {"service_id": shop["service_id"], "date_from": monday, "date_to": monday}
    assert len(client.get("/slots", params=params).json()) == 5

    url = f"/bookings/{created['id']}/cancel"
    assert client.post(url, params={"token": "wrong"}).status_code == 404
    assert client.post(url).status_code == 404
    done = client.post(url, params={"token": created["cancel_token"]})
    assert done.status_code == 200 and done.json()["status"] == "cancelled"
    assert client.post(url, params={"token": created["cancel_token"]}).status_code == 409
    assert len(client.get("/slots", params=params).json()) == 6
    assert client.post("/bookings", json=body).status_code == 201


def test_booking_rejections(
    client: TestClient, owner: dict[str, str], shop: dict[str, int], monday: date
) -> None:
    ok = booking_body(shop, local_iso(monday, 9))
    assert client.post("/bookings", json={**ok, "start": "2026-01-05T09:00:00"}).status_code == 422
    assert client.post("/bookings", json={**ok, "customer_email": "nope"}).status_code == 422
    assert client.post("/bookings", json={**ok, "customer_name": ""}).status_code == 422
    assert client.post("/bookings", json={**ok, "staff_id": 99}).status_code == 404
    assert client.post("/bookings", json={**ok, "service_id": 99}).status_code == 404
    assert client.post("/bookings", json={**ok, "start": local_iso(monday, 13)}).status_code == 422
    past = datetime.now(UTC) - timedelta(days=1)
    assert client.post("/bookings", json={**ok, "start": past.isoformat()}).status_code == 422
    far = next_weekday(0, 200)
    assert client.post("/bookings", json={**ok, "start": local_iso(far, 9)}).status_code == 422
    # staff does not offer this service
    svc2 = client.post(
        "/services", json={"name": "Dye", "duration_minutes": 60}, headers=owner
    ).json()
    assert client.post("/bookings", json={**ok, "service_id": svc2["id"]}).status_code == 422
    # a different weekday has no availability
    tuesday = monday + timedelta(days=1)
    assert client.post("/bookings", json={**ok, "start": local_iso(tuesday, 9)}).status_code == 422


def test_booking_with_non_ny_offset_is_normalised(
    client: TestClient, shop: dict[str, int], monday: date
) -> None:
    utc_start = datetime(monday.year, monday.month, monday.day, 9, tzinfo=NY).astimezone(UTC)
    r = client.post(
        "/bookings", json=booking_body(shop, utc_start.isoformat().replace("+00:00", "Z"))
    )
    assert r.status_code == 201
    assert datetime.fromisoformat(r.json()["start_utc"]) == utc_start


def test_time_off_blocks_slots_and_bookings(
    client: TestClient, owner: dict[str, str], shop: dict[str, int], monday: date
) -> None:
    start = datetime(monday.year, monday.month, monday.day, 9, tzinfo=NY)
    off = {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
    assert (
        client.post(f"/staff/{shop['staff_id']}/time-off", json=off, headers=owner).status_code
        == 201
    )
    params = {"service_id": shop["service_id"], "date_from": monday, "date_to": monday}
    assert len(client.get("/slots", params=params).json()) == 4
    assert (
        client.post("/bookings", json=booking_body(shop, local_iso(monday, 9))).status_code == 409
    )
    assert (
        client.post("/bookings", json=booking_body(shop, local_iso(monday, 10))).status_code == 201
    )


def test_staff_sees_only_own_bookings_owner_sees_all(
    client: TestClient, owner: dict[str, str], shop: dict[str, int], monday: date
) -> None:
    bob = client.post("/staff", json={"name": "Bob"}, headers=owner).json()["id"]
    client.put(f"/staff/{bob}/services", json={"service_ids": [shop["service_id"]]}, headers=owner)
    client.put(
        f"/staff/{bob}/availability",
        json={"windows": [{"weekday": 0, "start_time": "09:00", "end_time": "10:00"}]},
        headers=owner,
    )
    client.post("/bookings", json=booking_body(shop, local_iso(monday, 9)))
    client.post("/bookings", json={**booking_body(shop, local_iso(monday, 9)), "staff_id": bob})
    assert len(client.get("/bookings", headers=owner).json()) == 2
    me = make_staff_login(client, owner, bob, "bob@example.com")
    mine = client.get("/bookings", headers=me).json()
    assert len(mine) == 1 and mine[0]["staff_id"] == bob
    assert client.get("/bookings", params={"status": "cancelled"}, headers=owner).json() == []
    assert client.get("/bookings", params={"status": "bogus"}, headers=owner).status_code == 422


def test_staff_cancel_rules(
    client: TestClient, owner: dict[str, str], shop: dict[str, int], monday: date
) -> None:
    bob = client.post("/staff", json={"name": "Bob"}, headers=owner).json()["id"]
    booking = client.post("/bookings", json=booking_body(shop, local_iso(monday, 9))).json()
    me = make_staff_login(client, owner, bob, "bob@example.com")
    url = f"/bookings/{booking['id']}/staff-cancel"
    assert client.post(url, headers=me).status_code == 404  # someone else's booking
    assert client.post(url).status_code == 401
    assert client.post("/bookings/999/staff-cancel", headers=owner).status_code == 404
    assert client.post(url, headers=owner).json()["status"] == "cancelled"
    assert client.post(url, headers=owner).status_code == 409


def test_reminders_queued_then_cancelled(
    client: TestClient, owner: dict[str, str], shop: dict[str, int], monday: date
) -> None:
    b = client.post("/bookings", json=booking_body(shop, local_iso(monday, 9))).json()
    queued = client.get("/reminders", params={"status": "queued"}, headers=owner).json()
    assert sorted(r["kind"] for r in queued) == ["1h", "24h"]
    sends = {r["kind"]: datetime.fromisoformat(r["send_at"]) for r in queued}
    start = datetime.fromisoformat(b["start_utc"])
    assert sends["24h"] == start - timedelta(hours=24) and sends["1h"] == start - timedelta(hours=1)
    client.post(f"/bookings/{b['id']}/staff-cancel", headers=owner)
    assert client.get("/reminders", params={"status": "queued"}, headers=owner).json() == []
    assert len(client.get("/reminders", params={"status": "cancelled"}, headers=owner).json()) == 2
