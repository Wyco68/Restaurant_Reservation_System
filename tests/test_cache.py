"""Redis response cache (app/cache.py).

The rest of the suite runs with the cache off, so query counts stay exact.
These tests turn it on against a real Redis and are skipped without one:

    docker compose up -d redis
    TEST_REDIS_URL=redis://localhost:6379/15 pytest tests/test_cache.py
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from bson import ObjectId
from sqlalchemy import text

from app import cache
from app.db import mongo
from app.security import create_access_token

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest_asyncio.fixture
async def cached():
    before = cache.current_url()
    cache.configure(REDIS_URL)
    if not await cache.ping():
        cache.configure(before)
        pytest.skip(f"no Redis at {REDIS_URL}")
    # A new generation, so nothing from an earlier run can be a hit.
    await cache.invalidate("restaurants")
    await cache.invalidate("products")
    yield
    await cache.close()
    cache.configure(before)


async def test_second_search_is_a_hit(client, cached):
    first = await client.get("/api/v1/restaurants?limit=3&page=1")
    second = await client.get("/api/v1/restaurants?limit=3&page=1")
    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "HIT"
    assert second.json() == first.json()


async def test_parameter_order_shares_an_entry(client, cached):
    await client.get("/api/v1/products?limit=5&page=2")
    r = await client.get("/api/v1/products?page=2&limit=5")
    assert r.headers["x-cache"] == "HIT"


async def test_errors_are_not_stored(client, cached):
    for _ in range(2):
        r = await client.get("/api/v1/restaurants/999999999")
        assert r.status_code == 404
        assert r.headers["x-cache"] == "MISS"


async def test_availability_is_never_cached(client, cached):
    rid = (await client.get("/api/v1/restaurants?limit=1")).json()["items"][0]["id"]
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
    r = await client.get(
        f"/api/v1/restaurants/{rid}/availability",
        params={"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()},
    )
    assert r.status_code == 200
    assert "x-cache" not in r.headers


async def test_product_write_invalidates_the_catalogue(client, cached, db):
    with db.connect() as conn:
        staff_id = conn.execute(
            text("SELECT id FROM users WHERE role = 'staff' AND is_active LIMIT 1")
        ).scalar()
    if staff_id is None:
        pytest.skip("no seeded staff - run scripts/seed.py")
    staff = {"Authorization": f"Bearer {create_access_token(staff_id, 'staff')}"}

    rid = (await client.get("/api/v1/restaurants?limit=1")).json()["items"][0]["id"]
    url = f"/api/v1/products?restaurant_id={rid}&limit=100"
    await client.get(url)
    assert (await client.get(url)).headers["x-cache"] == "HIT"

    name = f"Cache test {uuid.uuid4().hex[:8]}"
    r = await client.post(
        "/api/v1/products",
        headers=staff,
        json={"restaurant_id": rid, "name": name, "category": "Side", "price": "1.00"},
    )
    assert r.status_code == 201
    try:
        after = await client.get(url)
        assert after.headers["x-cache"] == "MISS"
        assert name in {p["name"] for p in after.json()["items"]}
    finally:
        await mongo.products().delete_one({"_id": ObjectId(r.json()["_id"])})


async def test_redis_down_falls_back_to_the_database(client):
    before = cache.current_url()
    # Nothing listens on port 1.
    cache.configure("redis://127.0.0.1:1/0")
    try:
        first = await client.get("/api/v1/restaurants?limit=2")
        second = await client.get("/api/v1/restaurants?limit=2")
        assert first.status_code == second.status_code == 200
        assert first.headers["x-cache"] == "BYPASS"
        # Still bypassed during the back-off, without waiting on Redis again.
        assert second.headers["x-cache"] == "BYPASS"
    finally:
        await cache.close()
        cache.configure(before)
