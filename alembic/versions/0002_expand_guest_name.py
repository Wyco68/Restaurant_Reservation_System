"""CP2 step 1 of 5 - EXPAND: add first_name / last_name alongside guest_name.

team_project.pdf p.5, the required Expand-and-Contract pattern:

    1. EXPAND      <- THIS MIGRATION
    2. DUAL WRITE     app/services/reservations.py::create_reservation
    3. BACKFILL       scripts/backfill_names.py
    4. SWITCH READ    READ_NEW_NAME_FIELDS=true
    5. CONTRACT       revision 0003 (written at CP2, not before)

WHY THIS IS SAFE UNDER LIVE TRAFFIC
-----------------------------------
Both columns are added NULLABLE with NO DEFAULT. Since PostgreSQL 11 that is
a catalog-only change: no table rewrite, no data pages touched, and the
ACCESS EXCLUSIVE lock is held for microseconds rather than for the duration
of a full-table rewrite.

Adding `NOT NULL DEFAULT ''` instead would rewrite every row while holding
that lock, blocking all concurrent reads AND writes - exactly the downtime
this exercise exists to avoid.

Run scripts/traffic.py against POST /api/v1/reservations while applying
this and observe zero failed requests.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, no default, no server_default. Metadata-only.
    op.add_column("reservations", sa.Column("first_name", sa.String(60), nullable=True))
    op.add_column("reservations", sa.Column("last_name", sa.String(60), nullable=True))

    # Partial index supporting the batched backfill. It only covers rows
    # still awaiting migration, so it starts small, shrinks to empty as the
    # backfill progresses, and makes each batch's lookup an index scan
    # rather than a sequential scan of the whole table.
    #
    # CONCURRENTLY would avoid taking a write lock, but it cannot run inside
    # a transaction and Alembic wraps migrations in one. At this table size
    # the plain CREATE INDEX is instant. For a genuinely large table the
    # index would be created outside Alembic with CONCURRENTLY - noted here
    # because it is the honest answer if asked in the audit.
    op.create_index(
        "idx_reservations_backfill",
        "reservations",
        ["id"],
        postgresql_where=sa.text("first_name IS NULL"),
    )


def downgrade() -> None:
    # Fully reversible. Nothing has been dropped, so rolling back loses only
    # data written exclusively to the new columns - and dual-write guarantees
    # guest_name still holds every value.
    op.drop_index("idx_reservations_backfill", table_name="reservations")
    op.drop_column("reservations", "last_name")
    op.drop_column("reservations", "first_name")
