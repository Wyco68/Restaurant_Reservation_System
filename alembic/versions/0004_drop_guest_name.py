"""Step 5b of 5, Contract part two: drop guest_name.

Run this only after 0003 and after the API is in phase new_only, when
nothing reads or writes guest_name any more. See 0003 for why Contract is
split.

Steps:

  1. Stop if any reservation still has no first_name, because dropping
     guest_name would lose that name.
  2. Make first_name and last_name NOT NULL. A plain SET NOT NULL scans
     the table while blocking reads and writes, so we do it in steps:
       ADD CHECK ... NOT VALID      instant
       VALIDATE CONSTRAINT          scans, but does not block reads or writes
       SET NOT NULL                 PostgreSQL 12+ uses the CHECK, no scan
       DROP the CHECK               not needed any more
     Each statement commits on its own (autocommit block), so the first
     lock is not held during the scan.
  3. Drop the backfill's partial index, which is empty now.
  4. DROP COLUMN guest_name. PostgreSQL only marks it dropped, no rewrite.

The downgrade rebuilds guest_name from first_name and last_name. It is
only meant for practising the demo.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

reservations = sa.table(
    "reservations",
    sa.column("guest_name", sa.String),
    sa.column("first_name", sa.String),
    sa.column("last_name", sa.String),
)

# Fixed SQL statements with no values inserted into them.
NOT_NULL_STEPS = (
    "ALTER TABLE reservations ADD CONSTRAINT ck_reservations_first_name_nn "
    "CHECK (first_name IS NOT NULL) NOT VALID",
    "ALTER TABLE reservations VALIDATE CONSTRAINT ck_reservations_first_name_nn",
    "ALTER TABLE reservations ALTER COLUMN first_name SET NOT NULL",
    "ALTER TABLE reservations DROP CONSTRAINT ck_reservations_first_name_nn",
    "ALTER TABLE reservations ADD CONSTRAINT ck_reservations_last_name_nn "
    "CHECK (last_name IS NOT NULL) NOT VALID",
    "ALTER TABLE reservations VALIDATE CONSTRAINT ck_reservations_last_name_nn",
    "ALTER TABLE reservations ALTER COLUMN last_name SET NOT NULL",
    "ALTER TABLE reservations DROP CONSTRAINT ck_reservations_last_name_nn",
)


def upgrade() -> None:
    unmigrated = op.get_bind().scalar(
        sa.select(sa.func.count())
        .select_from(reservations)
        .where(reservations.c.first_name.is_(None))
    )
    if unmigrated:
        raise RuntimeError(
            f"{unmigrated} reservations have no first_name. "
            "Run scripts/backfill_names.py before contracting."
        )

    with op.get_context().autocommit_block():
        for statement in NOT_NULL_STEPS:
            op.execute(statement)

    op.drop_index("idx_reservations_backfill", table_name="reservations")
    op.drop_column("reservations", "guest_name")


def downgrade() -> None:
    op.add_column("reservations", sa.Column("guest_name", sa.String(120), nullable=True))
    op.execute(
        reservations.update().values(
            guest_name=sa.func.btrim(
                sa.func.concat_ws(" ", reservations.c.first_name, reservations.c.last_name)
            )
        )
    )
    op.create_index(
        "idx_reservations_backfill",
        "reservations",
        ["id"],
        postgresql_where=sa.text("first_name IS NULL"),
    )
    op.alter_column("reservations", "last_name", existing_type=sa.String(60), nullable=True)
    op.alter_column("reservations", "first_name", existing_type=sa.String(60), nullable=True)
