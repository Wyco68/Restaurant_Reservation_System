"""API acceptance tests.

Covers every core endpoint and the full status-code surface:
200, 201, 400, 401, 403, 404, 409, 422.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient


# ----------------------------------------------------------------- health


async def test_health_reports_both_engines(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["postgres"] is True, "PostgreSQL unreachable - is docker compose up?"
    assert body["mongodb"] is True, "MongoDB unreachable - is docker compose up?"


# ------------------------------------------------------- POST /users (201)


async def test_create_user_returns_201(client: AsyncClient, unique_email: str):
    r = await client.post(
        "/api/v1/users",
        json={"email": unique_email, "password": "ValidPass123!", "full_name": "Ada Lovelace"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == unique_email
    assert body["role"] == "customer"
    # The hash must never leave the server.
    assert "password" not in body
    assert "password_hash" not in body


async def test_duplicate_email_returns_409(client: AsyncClient, unique_email: str):
    payload = {"email": unique_email, "password": "ValidPass123!", "full_name": "First"}
    assert (await client.post("/api/v1/users", json=payload)).status_code == 201
    r = await client.post("/api/v1/users", json=payload)
    assert r.status_code == 409


async def test_short_password_rejected(client: AsyncClient, unique_email: str):
    r = await client.post(
        "/api/v1/users",
        json={"email": unique_email, "password": "short", "full_name": "Ada"},
    )
    assert r.status_code == 422  # Pydantic validation, before any handler runs


async def test_malformed_email_rejected(client: AsyncClient):
    r = await client.post(
        "/api/v1/users",
        json={"email": "not-an-email", "password": "ValidPass123!", "full_name": "Ada"},
    )
    assert r.status_code == 422


# -------------------------------------------------- GET /users/{id} (401)


async def test_get_user_without_token_returns_401(client: AsyncClient):
    r = await client.get("/api/v1/users/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 401


async def test_get_user_with_garbage_token_returns_401(client: AsyncClient):
    r = await client.get(
        "/api/v1/users/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer not.a.real.token"},
    )
    assert r.status_code == 401


async def test_get_own_profile_returns_200(client: AsyncClient, auth_headers: dict):
    r = await client.get("/api/v1/users/me/profile", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["role"] == "customer"


# ---------------------------------------------------- GET /products (200)


async def test_products_are_paginated(client: AsyncClient):
    r = await client.get("/api/v1/products?page=1&limit=5")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) <= 5
    assert body["page"] == 1
    assert body["limit"] == 5
    assert body["total"] >= 0
    assert set(body) == {"items", "page", "limit", "total", "pages"}


async def test_product_list_is_projected(client: AsyncClient):
    """List view must not carry description or attributes.

    Asserted as a test rather than trusted as a convention.
    """
    r = await client.get("/api/v1/products?limit=1")
    items = r.json()["items"]
    if items:
        assert "description" not in items[0]
        assert "attributes" not in items[0]


async def test_pagination_pages_differ(client: AsyncClient):
    p1 = (await client.get("/api/v1/products?page=1&limit=3")).json()["items"]
    p2 = (await client.get("/api/v1/products?page=2&limit=3")).json()["items"]
    if len(p1) == 3 and p2:
        assert {i["_id"] for i in p1}.isdisjoint({i["_id"] for i in p2})


async def test_limit_above_maximum_rejected(client: AsyncClient):
    r = await client.get("/api/v1/products?limit=5000")
    assert r.status_code == 422


async def test_create_product_requires_auth(client: AsyncClient):
    r = await client.post(
        "/api/v1/products",
        json={"restaurant_id": 1, "name": "Test Dish", "category": "Main", "price": 100},
    )
    assert r.status_code == 401


async def test_customer_cannot_create_product(client: AsyncClient, auth_headers: dict):
    r = await client.post(
        "/api/v1/products",
        json={"restaurant_id": 1, "name": "Test Dish", "category": "Main", "price": 100},
        headers=auth_headers,
    )
    assert r.status_code == 403


async def test_unknown_product_returns_404(client: AsyncClient):
    r = await client.get("/api/v1/products/0123456789abcdef01234567")
    assert r.status_code == 404


async def test_malformed_product_id_returns_400(client: AsyncClient):
    r = await client.get("/api/v1/products/not-an-objectid")
    assert r.status_code == 400


# ------------------------------------------------------ POST /orders (401)


async def test_create_order_requires_auth(client: AsyncClient):
    r = await client.post(
        "/api/v1/orders",
        json={"restaurant_id": 1, "items": [{"product_id": "0" * 24, "quantity": 1}]},
    )
    assert r.status_code == 401


async def test_order_with_unknown_product_returns_404(
    client: AsyncClient, auth_headers: dict
):
    r = await client.post(
        "/api/v1/orders",
        json={
            "restaurant_id": 1,
            "items": [{"product_id": "0123456789abcdef01234567", "quantity": 1}],
        },
        headers=auth_headers,
    )
    assert r.status_code == 404


async def test_order_with_empty_items_rejected(client: AsyncClient, auth_headers: dict):
    r = await client.post(
        "/api/v1/orders",
        json={"restaurant_id": 1, "items": []},
        headers=auth_headers,
    )
    assert r.status_code == 422


# ------------------------------------- POST /reservations - double booking


async def test_concurrent_booking_allows_exactly_one(
    client: AsyncClient, auth_headers: dict
):
    """THE double-booking test.

    Fire 15 simultaneous requests for one table and one window. The EXCLUDE
    constraint must let exactly one through.
    """
    r = await client.get("/api/v1/restaurants?limit=1")
    restaurants = r.json()["items"]
    if not restaurants:
        pytest.skip("no seeded restaurants - run scripts/seed.py")
    restaurant_id = restaurants[0]["id"]

    start = datetime.now(UTC).replace(
        minute=0, second=0, microsecond=0
    ) + timedelta(days=900)

    avail = await client.get(
        f"/api/v1/restaurants/{restaurant_id}/availability",
        params={
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(minutes=90)).isoformat(),
            "party_size": 2,
        },
    )
    slots = avail.json()
    if not slots:
        pytest.skip("no tables available")
    table_id = slots[0]["restaurant_table_id"]

    payload = {
        "restaurant_id": restaurant_id,
        "restaurant_table_id": table_id,
        "guest_name": "Race Condition",
        "party_size": 2,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(minutes=90)).isoformat(),
    }

    results = await asyncio.gather(
        *[
            client.post("/api/v1/reservations", json=payload, headers=auth_headers)
            for _ in range(15)
        ],
        return_exceptions=True,
    )
    codes = [getattr(r, "status_code", 0) for r in results]

    assert codes.count(201) == 1, f"expected exactly one 201, got {codes}"
    assert codes.count(409) == 14, f"expected fourteen 409, got {codes}"


async def test_reservation_end_before_start_rejected(
    client: AsyncClient, auth_headers: dict
):
    start = datetime.now(UTC) + timedelta(days=901)
    r = await client.post(
        "/api/v1/reservations",
        json={
            "restaurant_id": 1,
            "restaurant_table_id": 1,
            "guest_name": "Backwards Time",
            "party_size": 2,
            "starts_at": start.isoformat(),
            "ends_at": (start - timedelta(hours=1)).isoformat(),
        },
        headers=auth_headers,
    )
    assert r.status_code == 422


# ------------------------------------------------- migration helper logic


def test_split_guest_name_handles_real_cases():
    """Unit test for the one function both the API and the backfill share.

    If these two ever disagree about how a name splits, the backfill
    silently corrupts data - so the shared function gets its own test.
    """
    from app.services.reservations import split_guest_name

    assert split_guest_name("Ada Lovelace") == ("Ada", "Lovelace")
    # Split on the FIRST space only - multi-word surnames stay intact.
    assert split_guest_name("Mary Jane Watson") == ("Mary", "Jane Watson")
    # Mononym: keep it as the first name rather than dropping it.
    assert split_guest_name("Prince") == ("Prince", "")
    # Whitespace is normalized, not preserved.
    assert split_guest_name("  Ada   Lovelace  ") == ("Ada", "Lovelace")
    assert split_guest_name("") == ("", "")
    assert split_guest_name("   ") == ("", "")
