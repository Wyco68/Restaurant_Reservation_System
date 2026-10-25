"""Step 5a of 5, Contract part one: make guest_name nullable.

    1. Expand         revision 0002
    2. Dual write     phase dual_write
    3. Backfill       scripts/backfill_names.py
    4. Switch read    phase read_new
    5. Contract       this migration, then phase new_only, then revision 0004

Contract is split in two. If we dropped guest_name while the API still
writes it, every booking would fail. The API can only stop writing it
(phase new_only) once the column allows NULL:

    0003  guest_name DROP NOT NULL     API still writes it
          PUT /admin/migration new_only   API stops using guest_name
    0004  DROP COLUMN guest_name       nothing uses it any more

DROP NOT NULL only changes the catalog, so it is fast. It can be undone
until 0004 runs.

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
    # Bookings made in new_only have no guest_name. Fill it from the new
    # columns first, or NOT NULL cannot be added back.
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
