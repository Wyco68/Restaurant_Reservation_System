"""Step 1 of 5, Expand: add first_name and last_name next to guest_name.

    1. Expand         this migration
    2. Dual write     phase dual_write (app/migration.py)
    3. Backfill       scripts/backfill_names.py
    4. Switch read    phase read_new
    5. Contract       revision 0003, phase new_only, revision 0004

Both columns are nullable with no default. In PostgreSQL 11+ this only
changes the catalog, so the table is not rewritten and the lock is very
short. With NOT NULL DEFAULT '' every row would be rewritten while reads and
writes wait. Run scripts/traffic.py during this step to check that no
request fails.

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
    # Nullable and no default, so only the catalog changes.
    op.add_column("reservations", sa.Column("first_name", sa.String(60), nullable=True))
    op.add_column("reservations", sa.Column("last_name", sa.String(60), nullable=True))

    # Partial index for the backfill. It only has the rows that still need
    # migrating, so it gets smaller as the backfill runs.
    #
    # CREATE INDEX CONCURRENTLY would avoid the write lock, but it cannot run
    # inside Alembic's transaction. For our table size the normal CREATE
    # INDEX takes no noticeable time.
    op.create_index(
        "idx_reservations_backfill",
        "reservations",
        ["id"],
        postgresql_where=sa.text("first_name IS NULL"),
    )


def downgrade() -> None:
    # Can be undone. Because of dual write, guest_name still has every name.
    op.drop_index("idx_reservations_backfill", table_name="reservations")
    op.drop_column("reservations", "last_name")
    op.drop_column("reservations", "first_name")
