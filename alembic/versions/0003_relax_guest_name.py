"""Step 5a of 5 - CONTRACT, part one: make guest_name nullable.

The Expand-and-Contract pattern:

    1. EXPAND         revision 0002
    2. DUAL WRITE     phase dual_write
    3. BACKFILL       scripts/backfill_names.py
    4. SWITCH READ    phase read_new
    5. CONTRACT       THIS MIGRATION -> phase new_only -> revision 0004

WHY CONTRACT IS TWO MIGRATIONS
------------------------------
Dropping guest_name while the API still writes it turns every booking into
a 500. The API can only stop writing it (phase new_only) once the column
accepts NULL - otherwise the INSERT violates NOT NULL instead. So:

    0003  guest_name DROP NOT NULL     API still writes it; nothing changes
          PUT /admin/migration new_only   API stops touching guest_name
    0004  DROP COLUMN guest_name       nothing references it any more

DROP NOT NULL is a catalog-only change: no scan, no rewrite, a lock held for
microseconds. Fully reversible until 0004 runs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

reservations = sa.table(
    "reservations",
    sa.column("guest_name", sa.String),
    sa.column("first_name", sa.String),
    sa.column("last_name", sa.String),
)


def upgrade() -> None:
    op.alter_column("reservations", "guest_name", existing_type=sa.String(120), nullable=True)


def downgrade() -> None:
    # Rows booked in new_only have no guest_name. Rebuild it from the new
    # columns before NOT NULL comes back, or the ALTER fails on them.
    op.execute(
        reservations.update()
        .where(reservations.c.guest_name.is_(None))
        .values(
            guest_name=sa.func.btrim(
                sa.func.concat_ws(" ", reservations.c.first_name, reservations.c.last_name)
            )
        )
    )
    op.alter_column("reservations", "guest_name", existing_type=sa.String(120), nullable=False)
