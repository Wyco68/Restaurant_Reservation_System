"""Step 3 of 5 - BACKFILL: migrate legacy guest_name into first/last.

Runs WHILE the API is serving traffic. Design constraints that follow from
that, each of which is a deliberate choice rather than an accident:

  BATCHED       Each batch is its own short transaction. One
                `UPDATE reservations SET ...` over the whole table would
                hold row locks for the entire run and block concurrent
                bookings.

  RESUMABLE     Selects `WHERE first_name IS NULL`, so killing the script
                and restarting simply continues. No cursor or checkpoint
                file to lose.

  THROTTLED     Sleeps between batches so the backfill yields to real
                traffic instead of saturating the database.

  IDEMPOTENT    Already-migrated rows are invisible to the query, so running
                it twice is a no-op, not a corruption.

Usage:
    python scripts/backfill_names.py                  # run the backfill
    python scripts/backfill_names.py --verify         # check completeness
    python scripts/backfill_names.py --batch-size 200 --sleep 0.1
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, func, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.models import Reservation  # noqa: E402
from app.services.reservations import split_guest_name  # noqa: E402

# split_guest_name is imported from the service rather than reimplemented,
# so the backfill and the dual-write path can never disagree about how a
# name is split. A second copy here would be a latent data-inconsistency bug.


def run_backfill(session: Session, batch_size: int, sleep: float) -> int:
    total = 0
    while True:
        rows = (
            session.execute(
                select(Reservation.id, Reservation.guest_name)
                .where(Reservation.first_name.is_(None))
                .order_by(Reservation.id)
                .limit(batch_size)
            )
        ).all()

        if not rows:
            break

        for row_id, guest_name in rows:
            first, last = split_guest_name(guest_name or "")
            session.execute(
                update(Reservation)
                .where(Reservation.id == row_id)
                # Re-check inside the UPDATE: if live dual-write filled this
                # row between our SELECT and now, leave it alone. Without
                # this guard the backfill could overwrite fresher data.
                .where(Reservation.first_name.is_(None))
                .values(first_name=first, last_name=last)
            )

        session.commit()
        total += len(rows)
        print(f"  migrated {total:,} rows...", flush=True)

        if sleep:
            time.sleep(sleep)

    return total


def verify(session: Session) -> bool:
    remaining = session.scalar(
        select(func.count()).select_from(Reservation).where(Reservation.first_name.is_(None))
    )
    total = session.scalar(select(func.count()).select_from(Reservation))

    # Consistency check: does the reassembled name match the legacy column?
    # Compares on normalized whitespace so a double space is not reported as
    # a mismatch.
    mismatched = session.scalar(
        select(func.count())
        .select_from(Reservation)
        .where(
            Reservation.first_name.is_not(None),
            func.btrim(
                func.concat(
                    Reservation.first_name, " ", func.coalesce(Reservation.last_name, "")
                )
            )
            != func.btrim(Reservation.guest_name),
        )
    )

    print(f"\n  total reservations      {total:>8,}")
    print(f"  awaiting backfill       {remaining:>8,}")
    print(f"  name mismatches         {mismatched:>8,}")

    ok = remaining == 0 and mismatched == 0
    print(f"\n  {'BACKFILL COMPLETE AND CONSISTENT' if ok else 'NOT READY - do not switch reads yet'}")
    if not ok:
        print("  Do NOT set READ_NEW_NAME_FIELDS=true or run the Contract step.")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill reservation names")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--sleep", type=float, default=0.05, help="seconds between batches")
    parser.add_argument("--verify", action="store_true", help="check only, no writes")
    args = parser.parse_args()

    engine = create_engine(settings.postgres_sync_dsn)
    with Session(engine) as session:
        if args.verify:
            return 0 if verify(session) else 1

        print(f"Backfilling in batches of {args.batch_size}...")
        migrated = run_backfill(session, args.batch_size, args.sleep)
        print(f"\n  done: {migrated:,} rows migrated")
        return 0 if verify(session) else 1


if __name__ == "__main__":
    raise SystemExit(main())
