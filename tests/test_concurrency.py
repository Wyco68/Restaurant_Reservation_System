"""Concurrency verification (CP3 §3): parallel writes cannot corrupt a booking.

Two shared resources, two mechanisms:

  a table for a time window   EXCLUDE constraint     one INSERT wins, rest 409
  an existing reservation     optimistic, xmin       one UPDATE wins, rest 409/412

Every request is a real HTTP request with its own database session and
connection. Requests that merely run at the same time rarely overlap - each
transaction lasts about a millisecond - so the edit race holds the row lock
itself until every request has read, then lets go: the worst case, on every
run. The outcome is checked in the database, not only in the status codes.

    pytest tests/test_concurrency.py -v
    python tests/test_concurrency.py

Against a running server instead of in-process (same .env as the server):

    TABLEFLOW_API=http://localhost:8000 pytest tests/test_concurrency.py -v
"""

from __future__ import annotations

import asyncio
import os
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db import postgres
from app.main import app
from app.security import create_access_token

USERS = 20


@pytest_asyncio.fixture
async def api() -> AsyncClient:
    base = os.environ.get("TABLEFLOW_API")
    transport = None if base else ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=base or "http://test", timeout=30) as c:
        yield c
    # 20 requests outnumber the pool's 15 connections, so some waited - and
    # the pool's wait queue is now tied to this test's event loop. A fresh
    # pool for the next test.
    await postgres.engine.dispose()


@pytest.fixture
def customers(db) -> list[dict[str, str]]:
    """Bearer headers for 20 different seeded customers."""
    with db.connect() as conn:
        ids = conn.execute(text(
            "SELECT id FROM users WHERE role = 'customer' AND is_active ORDER BY email LIMIT :n"
        ), {"n": USERS}).scalars().all()
    if len(ids) < USERS:
        pytest.skip("needs 20 seeded customers - run scripts/seed.py")
    return [{"Authorization": f"Bearer {create_access_token(i, 'customer')}"} for i in ids]


async def free_table(api: AsyncClient, party_size: int = 2) -> tuple[int, int, datetime]:
    """A (restaurant, table, start) nobody holds, far enough out to be unused."""
    restaurants = (await api.get("/api/v1/restaurants?limit=20")).json()["items"]
    for _ in range(10):
        restaurant_id = random.choice(restaurants)["id"]
        start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(
            days=random.randint(10_000, 30_000), hours=random.randint(0, 23)
        )
        slots = (await api.get(
            f"/api/v1/restaurants/{restaurant_id}/availability",
            params={
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(minutes=90)).isoformat(),
                "party_size": party_size,
            },
        )).json()
        free = [s for s in slots if s["available"]]
        if free:
            return restaurant_id, free[0]["restaurant_table_id"], start
    pytest.skip(f"no table for {party_size}")


async def book(api: AsyncClient, headers: dict, party_size: int = 2) -> dict:
    restaurant_id, table_id, start = await free_table(api, party_size)
    r = await api.post("/api/v1/reservations", headers=headers, json={
        "restaurant_id": restaurant_id,
        "restaurant_table_id": table_id,
        "guest_name": "Race Condition",
        "party_size": party_size,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(minutes=90)).isoformat(),
    })
    assert r.status_code == 201, r.text
    return r.json()


def if_match(headers: dict, version: int) -> dict:
    return {**headers, "If-Match": f'"{version}"'}


async def all_at_once(db, reservation_id: int, requests: list) -> list:
    """Run the requests while this test holds the row lock, then release it.

    A PATCH reads without a lock, so every request reads the same version
    and passes If-Match, then queues on the lock at its UPDATE. Released,
    one UPDATE commits; the rest re-check xmin, match 0 rows, and get 409.
    Remove version_id_col from the model and all of them get 200.
    """
    with db.connect() as gate:
        gate.execute(
            text("SELECT 1 FROM reservations WHERE id = :id FOR UPDATE"), {"id": reservation_id}
        )
        tasks = [asyncio.ensure_future(r) for r in requests]
        await asyncio.sleep(1)       # every request is now waiting on the lock
        gate.rollback()
    return await asyncio.gather(*tasks)


# --------------------------------------------- the same table, 20 customers


async def test_twenty_customers_one_table_exactly_one_booking(api, customers, db):
    """The CP3 §3 scenario: 20 users race for the last free seat."""
    restaurant_id, table_id, start = await free_table(api)
    end = start + timedelta(minutes=90)

    async def attempt(headers: dict, i: int) -> int:
        r = await api.post("/api/v1/reservations", headers=headers, json={
            "restaurant_id": restaurant_id,
            "restaurant_table_id": table_id,
            "guest_name": f"Customer {i}",
            "party_size": 2,
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
        })
        return r.status_code

    codes = Counter(await asyncio.gather(*(attempt(h, i) for i, h in enumerate(customers))))
    print(f"\n  20 bookings for one table -> {dict(codes)}")

    assert codes[201] == 1, f"Race condition: {codes[201]} bookings for one table"
    assert codes[409] == USERS - 1
    with db.connect() as conn:
        held = conn.execute(text(
            "SELECT count(*) FROM reservations WHERE restaurant_table_id = :t"
            " AND status <> 'cancelled' AND starts_at < :e AND ends_at > :s"
        ), {"t": table_id, "s": start, "e": end}).scalar()
    assert held == 1


async def test_repeated_booking_races_never_fail_with_500(api, customers):
    """Concurrent INSERTs checked by the EXCLUDE constraint can deadlock;
    PostgreSQL aborts one. That must come back as 201 or 409 after a retry,
    never as a 500. One race deadlocks rarely, so run ten."""
    seen = Counter()
    for _ in range(10):
        restaurant_id, table_id, start = await free_table(api)

        async def attempt(headers: dict) -> int:
            r = await api.post("/api/v1/reservations", headers=headers, json={
                "restaurant_id": restaurant_id,
                "restaurant_table_id": table_id,
                "guest_name": "Repeat Race",
                "party_size": 2,
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(minutes=90)).isoformat(),
            })
            return r.status_code

        codes = Counter(await asyncio.gather(*(attempt(h) for h in customers)))
        seen += codes
        assert codes[201] == 1, f"race produced {dict(codes)}"
        assert codes[409] == USERS - 1, f"race produced {dict(codes)}"
    print(f"\n  10 races x 20 customers -> {dict(seen)}")


# ----------------------------------------- the same reservation, 20 edits


async def test_twenty_concurrent_edits_exactly_one_wins(api, customers, db):
    """Twenty tabs edit one booking from the same version: one write lands."""
    owner = customers[0]
    booking = await book(api, owner)

    async def edit(i: int):
        r = await api.patch(
            f"/api/v1/reservations/{booking['id']}",
            json={"guest_name": f"Editor {i}"},
            headers=if_match(owner, booking["version"]),
        )
        return r.status_code, r.json()

    results = await all_at_once(db, booking["id"], [edit(i) for i in range(USERS)])
    codes = Counter(code for code, _ in results)
    print(f"\n  20 edits of one version -> {dict(codes)}")

    winners = [body for code, body in results if code == 200]
    assert len(winners) == 1, f"Lost update: {len(winners)} edits accepted"
    assert codes[409] >= 1, "no request reached the version check in the UPDATE"
    assert codes[409] + codes[412] == USERS - 1

    now = (await api.get(f"/api/v1/reservations/{booking['id']}", headers=owner)).json()
    assert now["guest_name"] == winners[0]["guest_name"]
    assert now["version"] == winners[0]["version"] != booking["version"]


async def test_read_modify_write_loses_no_update(api, customers):
    """Six clients each add one guest, retrying on conflict. No increment may
    be lost: without the version check, two clients that read the same
    party_size would both write the same value."""
    owner = customers[0]
    booking = await book(api, owner, party_size=8)
    start_size = booking["party_size"]
    clients = 6

    async def add_one_guest() -> int:
        conflicts = 0
        while True:
            current = (await api.get(
                f"/api/v1/reservations/{booking['id']}", headers=owner
            )).json()
            r = await api.patch(
                f"/api/v1/reservations/{booking['id']}",
                json={"party_size": current["party_size"] + 1},
                headers=if_match(owner, current["version"]),
            )
            if r.status_code == 200:
                return conflicts
            assert r.status_code in (409, 412), r.text
            conflicts += 1

    conflicts = await asyncio.gather(*(add_one_guest() for _ in range(clients)))
    print(f"\n  {clients} increments, {sum(conflicts)} conflicts retried")

    final = (await api.get(f"/api/v1/reservations/{booking['id']}", headers=owner)).json()
    assert final["party_size"] == start_size + clients


# ------------------------------------------------------ the precondition


async def test_stale_version_returns_412_and_changes_nothing(api, customers):
    owner = customers[0]
    booking = await book(api, owner)
    url = f"/api/v1/reservations/{booking['id']}"

    first = await api.patch(url, json={"party_size": 3}, headers=if_match(owner, booking["version"]))
    assert first.status_code == 200
    assert first.headers["ETag"] == f'"{first.json()["version"]}"'

    stale = await api.patch(url, json={"party_size": 4}, headers=if_match(owner, booking["version"]))
    assert stale.status_code == 412
    assert (await api.get(url, headers=owner)).json()["party_size"] == 3


async def test_patch_without_if_match_returns_428(api, customers):
    owner = customers[0]
    booking = await book(api, owner)
    r = await api.patch(
        f"/api/v1/reservations/{booking['id']}", json={"party_size": 3}, headers=owner
    )
    assert r.status_code == 428


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-s"]))
