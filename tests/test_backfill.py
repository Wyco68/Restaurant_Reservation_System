"""scripts/backfill_names.py against real rows.

Legacy rows are inserted directly (guest_name only), backfilled, checked,
and deleted again, so the shared database is left as it was found.
"""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, insert, inspect, select
from sqlalchemy.orm import Session

from app.models import Reservation, User

t = Reservation.__table__

spec = importlib.util.spec_from_file_location(
    "backfill_names", Path(__file__).resolve().parents[1] / "scripts" / "backfill_names.py"
)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


@pytest.fixture
def legacy_rows(db, free_slot):
    """Factory inserting guest_name-only rows the way pre-Expand code wrote them."""
    columns = {c["name"] for c in inspect(db).get_columns("reservations")}
    if not {"guest_name", "first_name"} <= columns:
        pytest.skip("needs both guest_name and first_name (between 0002 and 0004)")
    with db.connect() as conn:
        user_id = conn.scalar(select(User.id).limit(1))
    ids: list[int] = []

    async def make(*names: str) -> list[int]:
        made = []
        for name in names:
            restaurant_id, table_id, start = await free_slot()
            with db.begin() as conn:
                made.append(conn.scalar(
                    insert(t).values(
                        user_id=user_id, restaurant_id=restaurant_id,
                        restaurant_table_id=table_id, guest_name=name, party_size=2,
                        starts_at=start, ends_at=start + timedelta(minutes=90),
                        status="confirmed",
                    ).returning(t.c.id)
                ))
        ids.extend(made)
        return made

    yield make
    with db.begin() as conn:
        conn.execute(delete(t).where(t.c.id.in_(ids)))


def names(db, ids):
    with db.connect() as conn:
        rows = conn.execute(
            select(t.c.id, t.c.first_name, t.c.last_name).where(t.c.id.in_(ids))
        ).all()
    return {r.id: (r.first_name, r.last_name) for r in rows}


async def test_pass_fills_legacy_rows_in_batches(
    client: AsyncClient, db, legacy_rows, capsys
):
    ids = await legacy_rows("Ada Lovelace", "Mary Jane Watson", "Prince", "  Alan   Turing ")

    with Session(db) as session:
        filled = backfill.run_pass(session, start_after=min(ids) - 1, batch_size=2, sleep=0)

    assert filled >= 4
    assert names(db, ids) == {
        ids[0]: ("Ada", "Lovelace"),
        ids[1]: ("Mary", "Jane Watson"),
        ids[2]: ("Prince", ""),
        ids[3]: ("Alan", "Turing"),
    }
    assert "checkpoint --start-after" in capsys.readouterr().out


async def test_rows_already_filled_are_not_overwritten(client: AsyncClient, db, legacy_rows):
    """A row dual-write filled first keeps its value - the UPDATE re-checks."""
    [rid] = await legacy_rows("Grace Hopper")
    with db.begin() as conn:
        conn.execute(t.update().where(t.c.id == rid).values(first_name="Fresh", last_name="Value"))

    with Session(db) as session:
        backfill.run_pass(session, start_after=rid - 1, batch_size=10, sleep=0)

    assert names(db, [rid]) == {rid: ("Fresh", "Value")}


async def test_start_after_resumes_past_the_checkpoint(client: AsyncClient, db, legacy_rows):
    first, second = await legacy_rows("Katherine Johnson", "Edsger Dijkstra")

    with Session(db) as session:
        backfill.run_pass(session, start_after=first, batch_size=10, sleep=0)

    filled = names(db, [first, second])
    assert filled[first] == (None, None)
    assert filled[second] == ("Edsger", "Dijkstra")


async def test_verify_flags_a_mismatch(client: AsyncClient, db, legacy_rows, capsys):
    [rid] = await legacy_rows("Barbara Liskov")
    with db.begin() as conn:
        conn.execute(t.update().where(t.c.id == rid).values(first_name="Wrong", last_name="Name"))

    with Session(db) as session:
        ok = backfill.verify(session)

    assert ok is False
    out = capsys.readouterr().out
    assert "name mismatches" in out
    assert "NOT READY" in out


def test_split_is_shared_with_the_api():
    from app.services import reservations

    assert backfill.split_guest_name is reservations.split_guest_name
