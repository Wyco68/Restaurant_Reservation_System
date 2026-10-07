"""PUT /api/v1/admin/migration - runtime phase control and its guards."""

from __future__ import annotations

from dataclasses import replace

import pytest
from httpx import AsyncClient

from app import migration
from app.migration import Phase, SchemaState

ADMIN = {"email": "user0000@tableflow.io", "password": "TableFlow123!"}  # seeded admin


@pytest.fixture
async def admin_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json=ADMIN)
    if r.status_code != 200:
        pytest.skip("seeded admin missing - run scripts/seed.py")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_requires_token(client: AsyncClient):
    r = await client.get("/api/v1/admin/migration")
    assert r.status_code == 401


async def test_customer_forbidden(client: AsyncClient, auth_headers: dict):
    r = await client.get("/api/v1/admin/migration", headers=auth_headers)
    assert r.status_code == 403


async def test_reports_phase_and_schema(client: AsyncClient, admin_headers: dict):
    r = await client.get("/api/v1/admin/migration", headers=admin_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == migration.current().value
    assert body["phase"] in body["allowed_phases"]
    assert set(body["writes"]) <= {"guest_name", "first_name", "last_name"}


async def test_allowed_phase_is_entered(client: AsyncClient, admin_headers: dict):
    allowed = (await client.get("/api/v1/admin/migration", headers=admin_headers)).json()[
        "allowed_phases"
    ]
    target = allowed[-1]

    r = await client.put("/api/v1/admin/migration", json={"phase": target}, headers=admin_headers)

    assert r.status_code == 200
    assert r.json()["phase"] == target
    assert migration.current().value == target


async def test_unsafe_phase_refused_with_reason(client: AsyncClient, admin_headers: dict):
    state = (await client.get("/api/v1/admin/migration", headers=admin_headers)).json()
    refused = [p for p in Phase if p.value not in state["allowed_phases"]]
    if not refused:
        pytest.skip("every phase is safe on this schema")

    r = await client.put(
        "/api/v1/admin/migration", json={"phase": refused[0].value}, headers=admin_headers
    )

    assert r.status_code == 409
    assert "Cannot enter" in r.json()["detail"]
    assert migration.current().value == state["phase"]


async def test_unknown_phase_rejected(client: AsyncClient, admin_headers: dict):
    r = await client.put("/api/v1/admin/migration", json={"phase": "yolo"}, headers=admin_headers)
    assert r.status_code == 422


# ------------------------------------------------ guard rules, no database

EXPANDED = SchemaState(  # after 0002 and a complete, consistent backfill
    guest_name_present=True, guest_name_nullable=False, new_columns_present=True,
    awaiting_backfill=0, mismatched=0, missing_legacy=0,
)


def allowed(state: SchemaState) -> set[Phase]:
    return {p for p in Phase if migration.refusal(p, state) is None}


def test_before_expand_only_legacy():
    state = replace(EXPANDED, new_columns_present=False, awaiting_backfill=None, mismatched=None)
    assert allowed(state) == {Phase.LEGACY}


def test_read_new_waits_for_backfill():
    assert Phase.READ_NEW not in allowed(replace(EXPANDED, awaiting_backfill=3))


def test_read_new_waits_for_consistency():
    assert Phase.READ_NEW not in allowed(replace(EXPANDED, mismatched=1))


def test_new_only_waits_for_0003():
    assert Phase.NEW_ONLY not in allowed(EXPANDED)
    assert Phase.NEW_ONLY in allowed(replace(EXPANDED, guest_name_nullable=True))


def test_rollback_to_legacy_stays_open_after_dual_write():
    assert Phase.LEGACY in allowed(EXPANDED)


def test_legacy_reads_refused_once_new_only_wrote_rows():
    state = replace(EXPANDED, guest_name_nullable=True, missing_legacy=5)
    assert allowed(state) == {Phase.READ_NEW, Phase.NEW_ONLY}


def test_after_0004_only_new_only():
    state = replace(
        EXPANDED, guest_name_present=False, guest_name_nullable=False,
        mismatched=None, missing_legacy=None,
    )
    assert allowed(state) == {Phase.NEW_ONLY}
