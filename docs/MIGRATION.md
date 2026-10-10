# Migration Runbook — `guest_name` → `first_name` + `last_name`

Run the five Expand-Contract steps (`Checkpoint_2_Zero_Downtime_Migration.pdf` §3) on a live API while `traffic.py` writes to it. Use it for the CP2 demo and every rehearsal before it. Why each step is built the way it is: [DESIGN.md](DESIGN.md#migration).

**Done means:** `traffic.py` ends with every check `PASS` — 0 5xx, 0 rejected, 0 connection errors, 0 wrong names, p99 < 500 ms (CP2 §4).

## Prerequisites

- Stack up and `.venv` installed — [README quick start](../README.md#quick-start)
- Three terminals in the project root, `.venv` activated

## 0. Reset to the legacy schema

```bash
alembic downgrade 0001
python scripts/seed.py --reset --reservations 20000
```

20,000 legacy bookings (`guest_name` only) give the backfill enough rows to watch. `alembic current` must print `0001`.

Then start the API — **one worker, no `--reload`**. The phase lives in process memory, and a reload mid-demo is a restart, which is downtime.

```bash
uvicorn app.main:app            # terminal 1
```

## 1–5. The migration

Terminal 2 — start traffic, leave it running:

```bash
python scripts/traffic.py --rps 25
```

Terminal 3 — one step at a time. Check the proof before the next step.

| Step | Command | Proof |
|---|---|---|
| — | `python scripts/phase.py` | `phase legacy`, `first/last_name absent` |
| 1 Expand | `alembic upgrade 0002` | `phase.py` → `first/last_name present`, `awaiting backfill` ≈ 20,000 |
| 2 Dual write | `python scripts/phase.py dual_write` | `writes guest_name, first_name, last_name`; `awaiting backfill` stops growing |
| 3 Backfill | `python scripts/backfill_names.py --verify` | **before:** `awaiting backfill` ≈ 20,000, `NOT READY` |
| | `python scripts/backfill_names.py` | one `batch … checkpoint --start-after N` line per 500 rows |
| | (printed at the end) | **after:** `awaiting 0`, `mismatches 0`, `BACKFILL COMPLETE AND CONSISTENT` |
| 4 Switch read | `python scripts/phase.py read_new` | `reads first_name, last_name` |
| 5 Contract | `alembic upgrade 0003` | `phase.py` → `guest_name nullable` |
| | `python scripts/phase.py new_only` | `writes first_name, last_name` |
| | `alembic upgrade 0004` | `phase.py` → `guest_name dropped` |

Terminal 2 — `Ctrl+C`. The summary must end `NO DOWNTIME - every request served correctly`.

Out-of-order steps are refused, not executed — worth showing once:

```bash
python scripts/phase.py read_new    # before step 3: 409 … still await backfill
python scripts/phase.py new_only    # before 0003:   409 … guest_name is still NOT NULL
```

### Verification queries

Same counts as `backfill_names.py --verify`, straight from PostgreSQL, for showing on screen:

```bash
docker exec -it tableflow_postgres psql -U dev_user -d tableflow -c "SELECT count(*) AS total, count(*) FILTER (WHERE first_name IS NULL) AS awaiting FROM reservations;"
```

Between steps 3 and 4, add the consistency check (expect `0`):

```bash
docker exec -it tableflow_postgres psql -U dev_user -d tableflow -c "SELECT count(*) AS mismatched FROM reservations WHERE first_name IS NOT NULL AND btrim(concat_ws(' ', first_name, last_name)) <> regexp_replace(btrim(guest_name), '\s+', ' ', 'g');"
```

## Rollback

**Change the phase first, then downgrade.** A downgrade under an API still in a later phase fails its requests — the guards check phase changes, not schema changes made behind the API's back.

| From | Do |
|---|---|
| 1 Expand | `alembic downgrade 0001` (API still `legacy`) |
| 2 Dual write | `phase.py legacy`, then `alembic downgrade 0001`. Names edited while back in `legacy` leave `first_name` stale; `read_new` stays refused until `0002` is redone |
| 3 Backfill | stop it; rerun later, or `--start-after <last checkpoint>` |
| 4 Switch read | `phase.py dual_write` |
| 5 after 0003 | `phase.py read_new`, then `alembic downgrade 0002` — rebuilds `guest_name` for rows written in `new_only` |
| 5 after 0004 | `alembic downgrade 0003` re-adds `guest_name` from the new columns. A rehearsal convenience; in production, restore the backup |

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `phase.py` prints `409 …` | step out of order | do what the message says |
| `alembic` fails with `lock timeout` | a long transaction held the table; [`env.py`](../alembic/env.py) gives up after 5 s rather than queueing traffic behind it | rerun the same command |
| `0004` raises `N reservations have no first_name` | backfill incomplete; the whole upgrade rolled back | step 3, then `0004` again |
| `traffic.py` shows `conn-err` | the API restarted | do not use `--reload`; restart and rerun from step 0 |
| `traffic.py` shows `4xx` | a phase the schema cannot serve — usually a downgrade after a phase change | `phase.py` to see the state; see Rollback |
| Seed fails with `UndefinedColumn` | old checkout of `seed.py` | pull `main` |
