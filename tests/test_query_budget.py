"""Query audit (CP3 §2.2): list endpoints run O(1) SQL, reads fetch only
the columns they return.

Every statement the app sends is captured from the engine. An endpoint is
O(1) when serving more rows does not send more statements; an N+1 shows up
here as a count that grows with the data.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import event

from app.db import postgres


@pytest.fixture
def sql():
    """Every SQL statement sent while the test runs, in order."""
    sent: list[str] = []

    def capture(conn, cursor, statement, *args):
        sent.append(statement)

    event.listen(postgres.engine.sync_engine, "before_cursor_execute", capture)
    yield sent
    event.remove(postgres.engine.sync_engine, "before_cursor_execute", capture)


async def statements(sql: list[str], call) -> list[str]:
    """The statements one request sent."""
    sql.clear()
    r = await call
    assert r.status_code == 200, r.text
    return list(sql)


async def book_somewhere(client: AsyncClient, headers: dict, restaurant_id: int) -> None:
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(
        days=random.randint(10_000, 30_000), hours=random.randint(0, 23)
    )
    end = start + timedelta(minutes=90)
    slots = (await client.get(
        f"/api/v1/restaurants/{restaurant_id}/availability",
        params={"starts_at": start.isoformat(), "ends_at": end.isoformat()},
    )).json()
    free = [s for s in slots if s["available"]]
    if not free:
        pytest.skip("no free table")
    r = await client.post("/api/v1/reservations", headers=headers, json={
        "restaurant_id": restaurant_id,
        "restaurant_table_id": free[0]["restaurant_table_id"],
        "guest_name": "Query Count",
        "party_size": 2,
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
    })
    assert r.status_code == 201, r.text


# ------------------------------------------------------------- O(1) lists


async def test_my_bookings_is_constant_in_bookings(client, auth_headers, sql):
    """1 booking or 5 at 5 different restaurants: the same statements."""
    restaurants = [r["id"] for r in (await client.get("/api/v1/restaurants?limit=5")).json()["items"]]
    list_call = lambda: client.get("/api/v1/reservations", headers=auth_headers)  # noqa: E731

    await book_somewhere(client, auth_headers, restaurants[0])
    one = await statements(sql, list_call())
    for rid in restaurants[1:]:
        await book_somewhere(client, auth_headers, rid)
    five = await statements(sql, list_call())

    assert len(five) == len(one) <= 2      # caller lookup + the list itself


async def test_my_orders_is_constant_in_orders(client, auth_headers, sql):
    menu = (await client.get("/api/v1/products?limit=50")).json()["items"]
    if not menu:
        pytest.skip("no seeded products")
    rid = menu[0]["restaurant_id"]
    dishes = [p["_id"] for p in menu if p["restaurant_id"] == rid]

    async def order(lines: int) -> None:
        r = await client.post("/api/v1/orders", headers=auth_headers, json={
            "restaurant_id": rid,
            "items": [{"product_id": d, "quantity": 1} for d in dishes[:lines]],
        })
        assert r.status_code == 201, r.text

    list_call = lambda: client.get("/api/v1/orders", headers=auth_headers)  # noqa: E731
    await order(1)
    one = await statements(sql, list_call())
    for lines in (2, 3, 2):
        await order(lines)
    four = await statements(sql, list_call())

    assert len(four) == len(one) <= 2


async def test_restaurant_search_is_constant_in_page_size(client, sql):
    small = await statements(sql, client.get("/api/v1/restaurants?limit=2"))
    large = await statements(sql, client.get("/api/v1/restaurants?limit=40"))
    assert len(small) == len(large) <= 2   # total count + one page


async def test_availability_is_constant_in_tables(client, sql):
    rid = (await client.get("/api/v1/restaurants?limit=1")).json()["items"][0]["id"]
    start = datetime.now(UTC) + timedelta(days=5_000)
    window = {"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()}

    many = await statements(sql, client.get(
        f"/api/v1/restaurants/{rid}/availability", params={**window, "party_size": 1}))
    few = await statements(sql, client.get(
        f"/api/v1/restaurants/{rid}/availability", params={**window, "party_size": 6}))
    assert len(many) == len(few) <= 2


# -------------------------------------------------------------- projection


def selected(statement: str) -> str:
    """The column list of a SELECT."""
    return statement.split(" FROM ", 1)[0]


async def test_search_never_reads_address_or_owner(client, sql):
    sent = await statements(sql, client.get("/api/v1/restaurants?limit=5"))
    page = selected(sent[-1])
    assert "restaurants.address" not in page
    assert "restaurants.owner_id" not in page


async def test_authenticated_requests_never_read_the_password_hash(
    client, auth_headers, sql
):
    """The caller lookup on every request, and GET /users/{id}, leave the
    bcrypt hash on disk. Only /auth/login reads it."""
    me = (await client.get("/api/v1/users/me/profile", headers=auth_headers)).json()
    sent = await statements(sql, client.get(f"/api/v1/users/{me['id']}", headers=auth_headers))
    sent += await statements(sql, client.get("/api/v1/reservations", headers=auth_headers))
    assert sent
    assert all("password_hash" not in selected(s) for s in sent)


async def test_login_reads_only_what_it_decides_on(client, auth_headers, unique_email, sql):
    sql.clear()
    r = await client.post(
        "/api/v1/auth/login", json={"email": unique_email, "password": "TestPassword123!"}
    )
    assert r.status_code == 200
    (lookup,) = sql
    for column in ("full_name", "phone", "created_at"):
        assert column not in selected(lookup)
