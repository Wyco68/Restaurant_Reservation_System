"""Step 3 of 5, Backfill: copy guest_name into first_name and last_name.

This runs while the API is serving traffic in phase dual_write, so:

  - Batches use a keyset cursor (id > last_id ORDER BY id LIMIT n) on the
    partial index idx_reservations_backfill. OFFSET would re-read all the
    skipped rows every batch.
  - Each batch is its own short transaction, so rows are only locked for
    one batch.
  - FOR UPDATE SKIP LOCKED skips rows a request is updating right now. A
    second pass picks them up.
  - Every batch prints the last id it committed, and --start-after resumes
    from there. Running again from the start is also safe, because finished
    rows no longer match first_name IS NULL.
  - The UPDATE checks first_name IS NULL again, so a row filled by dual
    write in the meantime is not overwritten.
  - It sleeps between batches to leave room for normal traffic.

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

# split_guest_name comes from the service, so the backfill and the API
# split names the same way.

t = Reservation.__table__

# One UPDATE per batch, run with a list of parameter sets.
FILL = (
    update(t)
    .where(t.c.id == bindparam("row_id"))
    .where(t.c.first_name.is_(None))
    .values(first_name=bindparam("first"), last_name=bindparam("last"))
)

MAX_PASSES = 5


def run_pass(session: Session, start_after: int, batch_size: int, sleep: float) -> int:
    """One pass over the table from `start_after` to the end. Returns rows filled."""
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
        # Rows left over were locked when we passed them. Do another pass, and
        # stop if a pass fills nothing.
        if left == 0 or filled == 0:
            break
        start_after = 0
    return total


def verify(session: Session) -> bool:
    """Check the data. Run it before and after the backfill."""
    columns = {c["name"] for c in inspect(session.connection()).get_columns("reservations")}
    total = session.scalar(select(func.count()).select_from(t))
    remaining = awaiting(session)

    print(f"\n  total reservations      {total:>8,}")
    print(f"  awaiting backfill       {remaining:>8,}")

    if "guest_name" not in columns:
        print("\n  guest_name dropped (0004) - nothing left to compare")
        return remaining == 0

    # Does first + last match guest_name? Uses the same check as the API's
    # read_new phase change.
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
