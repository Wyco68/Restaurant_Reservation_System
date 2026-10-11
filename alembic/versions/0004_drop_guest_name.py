"""Step 5b of 5 - CONTRACT, part two: drop guest_name.

Run only after 0003 and after the API is in phase new_only - nothing reads
or writes guest_name from that point, so dropping it is invisible to
traffic. See 0003 for why Contract is split.

What this does, in order:

  1. REFUSE if any reservation still lacks first_name. Dropping guest_name
     would destroy the only copy of that name.
  2. first_name / last_name become NOT NULL - they replace a NOT NULL
     column. A plain SET NOT NULL scans the whole table under ACCESS
     EXCLUSIVE, blocking every read and write for the scan. Instead:
       ADD CHECK ... NOT VALID      catalog only, instant
       VALIDATE CONSTRAINT          scans, but under SHARE UPDATE EXCLUSIVE,
                                    which does not block reads or writes
       SET NOT NULL                 PostgreSQL 12+ trusts the valid CHECK
                                    and skips the scan
       DROP the CHECK               redundant now
     Each statement commits on its own (autocommit block). In one
     transaction the first ALTER's ACCESS EXCLUSIVE lock would be held
     through the validation scan, defeating the point.
  3. Drop the backfill's partial index - it is empty and never used again.
  4. DROP COLUMN guest_name. PostgreSQL only marks the column dropped; no
     rewrite.

The downgrade rebuilds guest_name from first_name / last_name. That is
possible only because the split is lossless for names normalised to single
spaces; it exists for rehearsing the demo, not as a production rollback.

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

# Fixed DDL statements - written out in full, nothing interpolated.
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
