"""Controls the guest_name -> first_name / last_name migration while the app runs.

The schema changes are Alembic migrations. The app side is a phase in this
module, which an admin changes through the API, so no step needs a
restart.

    phase        writes                      reads                 needs
    -----------  --------------------------  --------------------  ---------------------------
    legacy       guest_name                  guest_name            guest_name
    dual_write   guest_name + first + last   guest_name            first/last columns (0002)
    read_new     guest_name + first + last   first + last          backfill finished
    new_only     first + last                first + last          guest_name nullable (0003)

Before a phase change is accepted we check the schema and the data, so a
step done in the wrong order is refused with a reason.

The phase is kept in memory, so each uvicorn worker has its own. That is
why we run a single worker for the demo.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import settings
from app.models.reservation import Reservation

log = logging.getLogger(__name__)


class Phase(str, enum.Enum):
    LEGACY = "legacy"
    DUAL_WRITE = "dual_write"
    READ_NEW = "read_new"
    NEW_ONLY = "new_only"


# Name columns written and read in each phase. The reservation service
# only uses these, so it never touches a column the phase does not have.
WRITE_COLUMNS: dict[Phase, tuple[str, ...]] = {
    Phase.LEGACY: ("guest_name",),
    Phase.DUAL_WRITE: ("guest_name", "first_name", "last_name"),
    Phase.READ_NEW: ("guest_name", "first_name", "last_name"),
    Phase.NEW_ONLY: ("first_name", "last_name"),
}

READ_COLUMNS: dict[Phase, tuple[str, ...]] = {
    Phase.LEGACY: ("guest_name",),
    Phase.DUAL_WRITE: ("guest_name",),
    Phase.READ_NEW: ("first_name", "last_name"),
    Phase.NEW_ONLY: ("first_name", "last_name"),
}

_current: Phase = Phase(settings.name_migration_phase)


def current() -> Phase:
    return _current


def write_columns() -> tuple[str, ...]:
    return WRITE_COLUMNS[_current]


def read_columns() -> tuple[str, ...]:
    return READ_COLUMNS[_current]


@dataclass(frozen=True)
class SchemaState:
    guest_name_present: bool
    guest_name_nullable: bool
    new_columns_present: bool
    awaiting_backfill: int | None   # rows with first_name IS NULL
    mismatched: int | None          # rows whose first + last disagree with guest_name
    missing_legacy: int | None      # rows with guest_name IS NULL


def name_mismatch():
    """True when first + last does not match guest_name, after trimming and
    collapsing spaces the same way split_guest_name does. The backfill's
    --verify uses the same rule.
    """
    r = Reservation
    rebuilt = func.btrim(func.concat_ws(" ", r.first_name, r.last_name))
    legacy = func.regexp_replace(func.btrim(r.guest_name), r"\s+", " ", "g")
    return r.first_name.is_not(None) & r.guest_name.is_not(None) & (rebuilt != legacy)


async def schema_state(conn: AsyncConnection) -> SchemaState:
    """Read the reservations schema and the counts the checks need."""
    columns = await conn.run_sync(
        lambda sync: {c["name"]: c for c in inspect(sync).get_columns("reservations")}
    )
    guest = columns.get("guest_name")
    has_new = "first_name" in columns and "last_name" in columns

    # Each count only uses columns we checked exist above.
    count = select(func.count()).select_from(Reservation)
    awaiting = (
        await conn.scalar(count.where(Reservation.first_name.is_(None))) if has_new else None
    )
    missing = (
        await conn.scalar(count.where(Reservation.guest_name.is_(None))) if guest else None
    )
    mismatched = (
        await conn.scalar(count.where(name_mismatch())) if has_new and guest else None
    )
    return SchemaState(
        guest_name_present=guest is not None,
        guest_name_nullable=bool(guest and guest["nullable"]),
        new_columns_present=has_new,
        awaiting_backfill=awaiting,
        mismatched=mismatched,
        missing_legacy=missing,
    )


def refusal(target: Phase, state: SchemaState) -> str | None:
    """The reason `target` is not allowed now, or None if it is."""
    writes, reads = WRITE_COLUMNS[target], READ_COLUMNS[target]

    if "guest_name" in writes + reads and not state.guest_name_present:
        return "guest_name has been dropped (0004); only new_only is possible"
    if "first_name" in writes + reads and not state.new_columns_present:
        return "first_name / last_name do not exist yet; run alembic upgrade 0002"
    if "guest_name" in reads and state.missing_legacy:
        return (
            f"{state.missing_legacy} reservations have no guest_name "
            "(written in new_only); reading guest_name would return null"
        )
    if "first_name" in reads and state.awaiting_backfill:
        return (
            f"{state.awaiting_backfill} reservations still await backfill; "
            "run scripts/backfill_names.py first"
        )
    # Catches names changed after going back to legacy: guest_name was
    # updated but first/last were not, and the backfill only fills NULLs.
    if "first_name" in reads and state.mismatched:
        return (
            f"{state.mismatched} reservations have first/last names that disagree "
            "with guest_name; see scripts/backfill_names.py --verify"
        )
    if "guest_name" not in writes and state.guest_name_present and not state.guest_name_nullable:
        return "guest_name is still NOT NULL; run alembic upgrade 0003 first"
    return None


def set_phase(target: Phase) -> None:
    global _current
    if target is not _current:
        log.warning("name migration phase: %s -> %s", _current.value, target.value)
    _current = target


async def reconcile_with_schema(engine: AsyncEngine) -> None:
    """At startup, fall back to a phase the current schema supports.

    For example, after a restart following the Contract step, .env may still
    say dual_write, which would make every request fail.
    """
    async with engine.connect() as conn:
        state = await schema_state(conn)
    if refusal(_current, state) is None:
        return
    for candidate in (Phase.NEW_ONLY, Phase.READ_NEW, Phase.DUAL_WRITE, Phase.LEGACY):
        if refusal(candidate, state) is None:
            log.warning(
                "configured phase %s does not fit the schema; starting in %s",
                _current.value,
                candidate.value,
            )
            set_phase(candidate)
            return
