"""Step 3 of 5 - BACKFILL: migrate legacy guest_name into first/last.

Runs WHILE the API is serving traffic in phase dual_write. Design
constraints that follow from that, each a deliberate choice:

  KEYSET CURSOR  Batches are `id > last_id ORDER BY id LIMIT n`, an index
                 range scan on the partial idx_reservations_backfill. Never
                 OFFSET, which rescans every skipped row on every batch.

  SHORT BATCHES  Each batch is its own transaction, so row locks last one
                 batch. One UPDATE over the whole table would hold them for
                 the entire run and block concurrent bookings.

  SKIP LOCKED    A row a live request is updating right now is skipped, not
                 waited on. A later pass picks it up.

  CHECKPOINT     Every batch prints the last id it committed. --start-after
                 resumes there. Restarting without it is also safe: the
                 `first_name IS NULL` predicate skips finished rows.

  RACE-SAFE      The UPDATE re-checks `first_name IS NULL`, so a row that
                 dual-write filled between our SELECT and UPDATE keeps the
                 fresher value.

  THROTTLED      Sleeps between batches so the backfill yields to traffic.

Usage:
    python scripts/backfill_names.py                  # run, then verify
    python scripts/backfill_names.py --verify         # check only, no writes
    python scripts/backfill_names.py --batch-size 200 --sleep 0.1
    python scripts/backfill_names.py --start-after 15000
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import bindparam, create_engine, func, inspect, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.migration import name_mismatch  # noqa: E402
from app.models import Reservation  # noqa: E402
from app.services.reservations import split_guest_name  # noqa: E402

# split_guest_name is imported from the service rather than reimplemented,
# so the backfill and the dual-write path can never disagree about how a
# name is split. A second copy here would be a latent data-inconsistency bug.

t = Reservation.__table__

# One statement, executed once per batch with a list of parameter sets.
FILL = (
    update(t)
    .where(t.c.id == bindparam("row_id"))
    .where(t.c.first_name.is_(None))
    .values(first_name=bindparam("first"), last_name=bindparam("last"))
)

MAX_PASSES = 5


def run_pass(session: Session, start_after: int, batch_size: int, sleep: float) -> int:
    """One sweep of the table from `start_after` to the end. Returns rows filled."""
    last_id, filled, batch_no = start_after, 0, 0
    while True:
        rows = session.execute(
            select(t.c.id, t.c.guest_name)
            .where(t.c.id > last_id, t.c.first_name.is_(None))
            .order_by(t.c.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        if not rows:
            session.commit()
            return filled

        params = []
        for row_id, guest_name in rows:
            first, last = split_guest_name(guest_name or "")
            params.append({"row_id": row_id, "first": first, "last": last})
        result = session.execute(FILL, params)
        session.commit()

        batch_no += 1
        filled += result.rowcount
        last_id = rows[-1].id
        print(f"  batch {batch_no:>4}  ids {rows[0].id}..{last_id}  "
              f"filled {filled:,}  checkpoint --start-after {last_id}", flush=True)

        if sleep:
            time.sleep(sleep)


def awaiting(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(t).where(t.c.first_name.is_(None)))


def run_backfill(session: Session, start_after: int, batch_size: int, sleep: float) -> int:
    total = 0
    for n in range(1, MAX_PASSES + 1):
        filled = run_pass(session, start_after, batch_size, sleep)
        total += filled
        left = awaiting(session)
        print(f"  pass {n}: filled {filled:,}, {left:,} still awaiting")
        # Leftovers are rows that were locked when the cursor passed them.
        # Sweep again from the start; stop if a pass makes no progress.
        if left == 0 or filled == 0:
            break
        start_after = 0
    return total


def verify(session: Session) -> bool:
    """Consistency report. Run before the backfill and after it."""
    columns = {c["name"] for c in inspect(session.connection()).get_columns("reservations")}
    total = session.scalar(select(func.count()).select_from(t))
    remaining = awaiting(session)

    print(f"\n  total reservations      {total:>8,}")
    print(f"  awaiting backfill       {remaining:>8,}")

    if "guest_name" not in columns:
        print("\n  guest_name dropped (0004) - nothing left to compare")
        return remaining == 0

    # Does the reassembled name match the legacy column? The rule is the
    # API's own read_new gate, imported so the two cannot disagree.
    mismatched = session.scalar(select(func.count()).select_from(t).where(name_mismatch()))
    new_only = session.scalar(
        select(func.count()).select_from(t).where(t.c.guest_name.is_(None))
    )
    print(f"  name mismatches         {mismatched:>8,}")
    print(f"  written in new_only     {new_only:>8,}")

    ok = remaining == 0 and mismatched == 0
    print(f"\n  {'BACKFILL COMPLETE AND CONSISTENT' if ok else 'NOT READY - do not switch reads yet'}")
    if not ok:
        print("  The API refuses phase read_new until this passes.")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill reservation names")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--sleep", type=float, default=0.05, help="seconds between batches")
    parser.add_argument("--start-after", type=int, default=0, help="resume after this id")
    parser.add_argument("--verify", action="store_true", help="check only, no writes")
    args = parser.parse_args()

    engine = create_engine(settings.postgres_sync_dsn)
    with Session(engine) as session:
        if args.verify:
            return 0 if verify(session) else 1

        print(f"Backfilling in batches of {args.batch_size}...")
        migrated = run_backfill(session, args.start_after, args.batch_size, args.sleep)
        print(f"\n  done: {migrated:,} rows migrated")
        return 0 if verify(session) else 1


if __name__ == "__main__":
    raise SystemExit(main())
