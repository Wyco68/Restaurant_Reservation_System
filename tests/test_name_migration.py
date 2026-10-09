"""The API side of the guest_name migration: dual-write and switch-read.

Each test puts the app in one phase and checks the columns in the database
directly - the API response alone cannot show which column a value came
from. Tests skip when the live schema cannot serve the phase (for example
legacy after 0004 has dropped guest_name).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, inspect, select, update

from app import migration
from app.migration import Phase
from app.models import Reservation

t = Reservation.__table__


def columns(db) -> dict[str, dict]:
    return {c["name"]: c for c in inspect(db).get_columns("reservations")}


def require(db, *needed: str, nullable_guest: bool = False) -> None:
    present = columns(db)
    missing = [c for c in needed if c not in present]
    if missing:
        pytest.skip(f"schema has no {', '.join(missing)}")
    if nullable_guest and not present.get("guest_name", {}).get("nullable", True):
        pytest.skip("guest_name is still NOT NULL (0003 not applied)")


def stored(db, rid: int) -> dict:
    with db.connect() as conn:
        cols = [c for c in (t.c.guest_name, t.c.first_name, t.c.last_name) if c.name in columns(db)]
        return dict(conn.execute(select(*cols).where(t.c.id == rid)).mappings().one())


@pytest.fixture
def created(db):
    ids: list[int] = []
    yield ids
    with db.begin() as conn:
        conn.execute(delete(t).where(t.c.id.in_(ids)))


async def book(client, headers, free_slot, created, name: str) -> dict:
    restaurant_id, table_id, start = await free_slot()
    r = await client.post(
        "/api/v1/reservations",
        json={
            "restaurant_id": restaurant_id,
            "restaurant_table_id": table_id,
            "guest_name": name,
            "party_size": 2,
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(minutes=90)).isoformat(),
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    created.append(r.json()["id"])
    return r.json()


async def test_legacy_writes_only_guest_name(
    client: AsyncClient, auth_headers: dict, free_slot, created, db
):
    require(db, "guest_name", "first_name")
    migration.set_phase(Phase.LEGACY)

    body = await book(client, auth_headers, free_slot, created, "Ada Lovelace")

    assert body["guest_name"] == "Ada Lovelace"
    assert stored(db, body["id"]) == {
        "guest_name": "Ada Lovelace", "first_name": None, "last_name": None,
    }


async def test_dual_write_fills_both_on_create(
    client: AsyncClient, auth_headers: dict, free_slot, created, db
):
    require(db, "guest_name", "first_name")
    migration.set_phase(Phase.DUAL_WRITE)

    body = await book(client, auth_headers, free_slot, created, "Mary Jane Watson")

    assert stored(db, body["id"]) == {
        "guest_name": "Mary Jane Watson", "first_name": "Mary", "last_name": "Jane Watson",
    }


async def test_dual_write_fills_both_on_update(
    client: AsyncClient, auth_headers: dict, free_slot, created, db
):
    require(db, "guest_name", "first_name")
    migration.set_phase(Phase.DUAL_WRITE)
    body = await book(client, auth_headers, free_slot, created, "Grace Hopper")

    r = await client.patch(
        f"/api/v1/reservations/{body['id']}", json={"guest_name": "Alan Turing"}, headers=auth_headers
    )

    assert r.status_code == 200
    assert r.json()["guest_name"] == "Alan Turing"
    assert stored(db, body["id"]) == {
        "guest_name": "Alan Turing", "first_name": "Alan", "last_name": "Turing",
    }


async def test_switch_read_ignores_guest_name(
    client: AsyncClient, auth_headers: dict, free_slot, created, db
):
    """Corrupt guest_name behind the API's back: read_new must not notice."""
    require(db, "guest_name", "first_name")
    migration.set_phase(Phase.DUAL_WRITE)
    body = await book(client, auth_headers, free_slot, created, "Barbara Liskov")
    with db.begin() as conn:
        conn.execute(update(t).where(t.c.id == body["id"]).values(guest_name="Tampered"))

    r = await client.get(f"/api/v1/reservations/{body['id']}", headers=auth_headers)
    assert r.json()["guest_name"] == "Tampered"           # dual_write reads legacy

    migration.set_phase(Phase.READ_NEW)
    r = await client.get(f"/api/v1/reservations/{body['id']}", headers=auth_headers)
    assert r.json()["guest_name"] == "Barbara Liskov"     # read_new reads new only


async def test_new_only_leaves_guest_name_null(
    client: AsyncClient, auth_headers: dict, free_slot, created, db
):
    require(db, "first_name", nullable_guest=True)
    migration.set_phase(Phase.NEW_ONLY)

    body = await book(client, auth_headers, free_slot, created, "Prince")

    assert body["guest_name"] == "Prince"
    row = stored(db, body["id"])
    assert (row["first_name"], row["last_name"]) == ("Prince", "")
    assert row.get("guest_name") is None
