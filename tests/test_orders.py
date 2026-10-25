"""Orders: status changes with a row lock, and the order list.

Our transactions take about 1 ms, so requests sent together often do not
overlap. The race tests lock the order's row themselves, send all the
requests, and release the lock once they are all waiting. Without
with_for_update() in advance_status() these tests fail.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text

from app.db import postgres
from app.security import create_access_token

RACERS = 20


@pytest_asyncio.fixture
async def staff(db) -> dict[str, str]:
    with db.connect() as conn:
        staff_id = conn.execute(text(
            "SELECT id FROM users WHERE role = 'staff' AND is_active LIMIT 1"
        )).scalar()
    if staff_id is None:
        pytest.skip("no seeded staff - run scripts/seed.py")
    yield {"Authorization": f"Bearer {create_access_token(staff_id, 'staff')}"}
    # The races use more requests than the pool has connections, so make a
    # new pool for the next test.
    await postgres.engine.dispose()


@pytest_asyncio.fixture
async def place_order(client: AsyncClient, auth_headers: dict):
    """Returns a function that places an order for the test user."""
    menu = (await client.get("/api/v1/products?limit=100")).json()["items"]
    if not menu:
        pytest.skip("no seeded products - run scripts/seed.py")
    restaurant_id = menu[0]["restaurant_id"]
    dishes = [p for p in menu if p["restaurant_id"] == restaurant_id]

    async def make(lines: int = 1) -> dict:
        r = await client.post("/api/v1/orders", headers=auth_headers, json={
            "restaurant_id": restaurant_id,
            "items": [{"product_id": p["_id"], "quantity": 1} for p in dishes[:lines]],
        })
        assert r.status_code == 201, r.text
        return r.json()

    return make


async def move(client: AsyncClient, headers: dict, order_id: int, to: str) -> int:
    r = await client.patch(f"/api/v1/orders/{order_id}/status", json={"status": to}, headers=headers)
    return r.status_code


async def all_at_once(db, order_id: int, requests: list[Awaitable[int]]) -> list[int]:
    """Send the requests while this test holds the order's row lock, then
    release it so they all compete at the same moment.
    """
    with db.connect() as gate:
        gate.execute(text("SELECT 1 FROM orders WHERE id = :id FOR UPDATE"), {"id": order_id})
        tasks = [asyncio.ensure_future(r) for r in requests]
        await asyncio.sleep(1)       # by now every request waits on the lock
        gate.rollback()
    return await asyncio.gather(*tasks)


async def test_twenty_identical_changes_exactly_one_applies(client, staff, place_order, db):
    """Without the lock all twenty would read 'placed', pass the check and
    save, so the order would be started twenty times.
    """
    order = await place_order()

    codes = Counter(await all_at_once(db, order["id"], [
        move(client, staff, order["id"], "preparing") for _ in range(RACERS)
    ]))
    print(f"\n  20 x placed->preparing -> {dict(codes)}")

    assert codes[200] == 1
    assert codes[409] == RACERS - 1


async def test_racing_ready_and_cancel_one_wins(client, staff, place_order, db):
    """Half the staff press ready and half press cancel. Whichever commits
    first, the other change is no longer allowed.
    """
    order = await place_order()
    assert await move(client, staff, order["id"], "preparing") == 200

    targets = ["ready", "cancelled"] * (RACERS // 2)
    results = await all_at_once(db, order["id"], [
        move(client, staff, order["id"], t) for t in targets
    ])
    won = [t for t, code in zip(targets, results) if code == 200]
    print(f"\n  10 x ready vs 10 x cancelled -> winner {won}")

    assert len(won) == 1
    assert Counter(results)[409] == RACERS - 1
    final = await client.get(f"/api/v1/orders/{order['id']}", headers=staff)
    assert final.json()["status"] == won[0]


async def test_illegal_transition_returns_409(client, staff, place_order):
    order = await place_order()
    assert await move(client, staff, order["id"], "completed") == 409
    assert await move(client, staff, order["id"], "cancelled") == 200
    assert await move(client, staff, order["id"], "preparing") == 409


async def test_customer_cannot_change_status(client, auth_headers, place_order):
    order = await place_order()
    assert await move(client, auth_headers, order["id"], "preparing") == 403


async def test_list_my_orders_summarises_in_sql(client, auth_headers, place_order):
    first = await place_order(lines=1)
    second = await place_order(lines=2)

    r = await client.get("/api/v1/orders", headers=auth_headers)
    assert r.status_code == 200
    listed = r.json()
    assert [o["id"] for o in listed] == [second["id"], first["id"]]
    assert [o["item_count"] for o in listed] == [len(second["items"]), 1]
    assert listed[0]["restaurant_name"]
    assert "items" not in listed[0]


async def test_list_my_orders_requires_auth(client):
    assert (await client.get("/api/v1/orders")).status_code == 401
