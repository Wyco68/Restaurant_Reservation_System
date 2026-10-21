"""The Reservation mapping never names a migration column it was not asked
to, and never writes over a version it did not read.

If any of the column tests fail, the API breaks on a schema where that
column does not exist - before 0002 or after 0004.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, InvalidRequestError
from sqlalchemy.orm import Session, undefer
from sqlalchemy.orm.exc import StaleDataError

from app.models import Reservation

NAMES = ("guest_name", "first_name", "last_name")


def sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_plain_select_omits_every_name_column():
    compiled = sql(select(Reservation))
    for column in NAMES:
        assert column not in compiled


def test_undefer_loads_only_the_named_column():
    compiled = sql(select(Reservation).options(undefer(Reservation.first_name)))
    assert "first_name" in compiled
    assert "guest_name" not in compiled


def test_orm_insert_omits_unset_name_columns(db):
    """FetchedValue: an unset column is left out of the INSERT, not sent as NULL.

    The INSERT is captured and rolled back; whether the database accepts it
    (FK, NOT NULL) does not matter here, only what the ORM asked for.
    """
    sent: list[str] = []
    with db.connect() as conn:
        event.listen(conn, "before_cursor_execute", lambda *a: sent.append(a[2]))
        with Session(bind=conn) as session:
            now = datetime.now(UTC)
            session.add(Reservation(
                user_id=uuid.uuid4(), restaurant_id=1, restaurant_table_id=1, party_size=2,
                starts_at=now, ends_at=now + timedelta(hours=1),
                first_name="Ada", last_name="Lovelace",
            ))
            try:
                session.flush()
            except DBAPIError:
                pass
            session.rollback()

    insert_sql = next(s for s in sent if s.startswith("INSERT INTO reservations"))
    assert "first_name" in insert_sql
    assert "guest_name" not in insert_sql
    assert "RETURNING" not in insert_sql or "guest_name" not in insert_sql.split("RETURNING")[1]


def test_unloaded_name_column_raises_instead_of_lazy_loading(db):
    with Session(db) as session:
        r = session.scalar(select(Reservation).limit(1))
        if r is None:
            pytest.skip("no reservations - run scripts/seed.py")
        with pytest.raises(InvalidRequestError, match="raiseload"):
            _ = r.guest_name


# ------------------------------------------------- optimistic concurrency


def test_update_is_guarded_by_xmin(db):
    """Every ORM UPDATE carries the version it read and returns the new one."""
    sent: list[str] = []
    with db.connect() as conn:
        event.listen(conn, "before_cursor_execute", lambda *a: sent.append(a[2]))
        with Session(bind=conn) as session:
            r = session.scalar(select(Reservation).limit(1))
            if r is None:
                pytest.skip("no reservations - run scripts/seed.py")
            r.party_size = r.party_size % 20 + 1
            session.flush()
            session.rollback()

    update_sql = next(s for s in sent if s.startswith("UPDATE reservations"))
    assert "reservations.xmin = " in update_sql
    assert "RETURNING reservations.xmin" in update_sql


def test_stale_version_raises_instead_of_overwriting(db):
    """Two sessions read the same version; the second write must fail."""
    with Session(db) as first, Session(db) as second:
        a = first.scalar(select(Reservation).order_by(Reservation.id.desc()).limit(1))
        if a is None:
            pytest.skip("no reservations - run scripts/seed.py")
        b = second.get(Reservation, a.id)
        assert a.version == b.version
        original = a.party_size

        a.party_size = original % 20 + 1
        first.commit()
        assert a.version != b.version is not None

        b.party_size = original % 20 + 1
        with pytest.raises(StaleDataError):
            second.commit()

        a.party_size = original
        first.commit()
