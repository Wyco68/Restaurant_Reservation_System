# Architecture & Design Decisions

Why the system is built this way. Schema reference: [DATA_MODEL.md](DATA_MODEL.md). Endpoint reference: [API_CONTRACT.md](API_CONTRACT.md). Team process: [../CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Context and goals

TableFlow is a restaurant booking and ordering platform on a hybrid database backend, with schema evolution performed without downtime.

Three problems drive the design: hybrid persistence across a relational and a document store, Expand-Contract migration under live traffic, and concurrency control. All three arise from the domain rather than being bolted on — reservations give a natural contention point (one table, one time slot, many requesters) and menu items give a naturally document-shaped entity.

### Scope

In: accounts with JWT auth and three roles, restaurant search, table reservation with guaranteed double-booking prevention, menu catalogue with dynamic attributes, pre-orders spanning both databases, reviews, telemetry, seed data above both 1,000-record minimums.

Out, and why:

| Excluded | Reason |
|---|---|
| Payment processing | PCI scope, orthogonal to the data problems |
| Delivery, driver tracking | An entire second domain |
| External map/restaurant APIs | Network dependency that can fail during a demo |
| AI recommendations | Unbounded effort, no payoff for the core problems |
| Real-time chat | WebSockets are orthogonal to the dual-database objective |

Deferred, not deleted: `opening_hours`, `order_status_history`, analytics endpoints, staff dashboard. The original scope was roughly four times what this team could finish in the time available, and the dominant risk on a fixed deadline is shipping nothing rather than shipping something narrow. Analytics lands alongside the performance work, which supplies the projections it needs.

---

## Technology

| Layer | Choice |
|---|---|
| Backend | Python / FastAPI |
| Relational | PostgreSQL 15 |
| Document | MongoDB 6.0 |
| Frontend | Vanilla JS / HTML5 |
| ORM | SQLAlchemy 2.x + Alembic |
| Drivers | psycopg 3, Motor |
| Containers | Docker Compose |

**On raw SQL.** Raw SQL string concatenation is banned in this codebase as an injection vector. Every query is a parameterized SQLAlchemy Core/ORM construct — no f-string or `%`-formatted SQL anywhere. Literal SQL appears in two places, both fixed strings with no interpolation: `docker/initdb/01_extensions.sql`, and `op.execute()` in migrations for the `EXCLUDE` constraint and partial indexes, which SQLAlchemy's DDL layer does not express as cleanly. The rule distinguishes developer-written DDL from SQL assembled out of request data; only the latter is an injection vector.

---

## Structure

```
app/
  main.py          app factory, router mounts, /health
  config.py        pydantic-settings, environment only
  security.py      bcrypt, JWT, role dependencies
  db/              connection lifecycle, session dependency, index creation
  models/          SQLAlchemy tables, constraints, indexes
  schemas/         Pydantic validation and serialization
  routers/         HTTP: routes, status codes, auth, simple CRUD
  services/        non-trivial logic only: reservations, orders
alembic/versions/  every schema change, forward and reverse
scripts/           operational tooling run by a human
tests/             integration tests against the real engines
frontend/          vanilla JS
```

Routers hold HTTP concerns and simple CRUD; services hold logic that is genuinely multi-step.

**No `repositories/` layer.** The `AsyncSession` and the Motor collection handle already sit between the domain and the driver — a repository class over them is an abstraction over an abstraction. The pattern earns its place when there are multiple implementations or the ORM must not leak into the domain; neither applies here, and the tests run against real engines by design because a mock cannot prove an `EXCLUDE` constraint works. Adding it would mean four more files and no new capability.

This is a deliberate deviation from Controller→Service→Repository. Reversing it is mechanical if it is ever wanted: move each `select()` out of its router into a `repositories/` module.

---

## Containers

| Requirement | Implementation |
|---|---|
| Root compose file | `./docker-compose.yml` |
| PostgreSQL on 5432 | `"${POSTGRES_PORT:-5432}:5432"` |
| PostgreSQL persistence | named volume `postgres_data` |
| Initial DDL scripts | `./docker/initdb` → `/docker-entrypoint-initdb.d` |
| MongoDB on 27017 | `"${MONGO_PORT:-27017}:27017"` |
| MongoDB persistence | named volume `mongo_data` |

Two details worth calling out:

- **Healthchecks** on both services, so `docker compose ps` reports *healthy* rather than merely *running* — the difference between "container started" and "database accepts connections", which is what produces a failed demo.
- **No hard-coded credentials.** Every value comes from `.env`, which is gitignored. A named bridge network keeps the services addressable by name.

Persistence is verified, not assumed:

```bash
docker compose down && docker compose up -d
python scripts/seed.py --verify     # counts unchanged
```

---

## Concurrency

One table, one 19:00 slot, twenty simultaneous requests. Exactly one must succeed.

The naive implementation is a check-then-act race:

```python
if not has_conflict(table_id, start, end):   # T1 and T2 both read "free"
    insert_reservation(...)                  # T1 and T2 both insert
```

No amount of care in application code closes this, because the window is *between two statements*.

### Option A — row locking

`SELECT ... FOR UPDATE` on the table row, then check overlap in Python. Correct, and a reasonable answer.

Against it: correctness lives in application code. The admin panel, the seed script, a future bulk import, a teammate's new endpoint — each is a fresh chance to forget the lock and silently reintroduce the bug. The lock is also on the table row, a proxy for the real resource, so it serializes bookings for that table even at non-overlapping times.

### Option B — exclusion constraint (chosen)

```sql
EXCLUDE USING gist (
    restaurant_table_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
) WHERE (status <> 'cancelled')
```

Overlap becomes structurally impossible, whatever the application does. Every write path is covered, including ones not yet written. No lock ordering, no deadlock design. Only genuinely overlapping windows block, so concurrency stays maximal, and the GiST index also serves availability queries.

Costs: needs `btree_gist`, and `IntegrityError` must be translated to `409`.

### Option C — application validation alone

Rejected. This is the race above. It appears to work in testing precisely because single-user testing never produces the interleaving that breaks it.

### Decision

Option B. One line in a migration, impossible to defeat from application code, and the easiest to reason about. Option A remains available as belt-and-braces if profiling ever shows constraint violations are hot — they are not, because the constraint only fires on genuine contention.

The pre-flight checks in `create_reservation` (table exists, belongs to the restaurant, capacity sufficient) produce friendly `400`/`404` messages. They are not the defence; deleting them would degrade error messages, not correctness.

### Proof

```bash
pytest tests/test_api.py::test_concurrent_booking_allows_exactly_one
python scripts/traffic.py --mode concurrent --burst 20
```

Exactly one `201`, nineteen `409`, one row. There is a third proof for free: `seed.py` inserts 600 reservations, so if its slot generator ever produced an overlap the seed itself would fail.

---

## Migration

Target: `reservations.guest_name` → `first_name` + `last_name`, with booking traffic running throughout.

| Step | Artifact | Reversible |
|---|---|---|
| 1 Expand | `alembic/versions/0002_expand_guest_name.py` | yes, `downgrade` |
| 2 Dual write | `app/services/reservations.py::create_reservation` | yes, code revert |
| 3 Backfill | `scripts/backfill_names.py` | yes, idempotent |
| 4 Switch read | `READ_NEW_NAME_FIELDS` env flag | yes, flip the flag |
| 5 Contract | `0003_drop_guest_name.py`, not yet written | **no — runs last** |

### Expand

Both columns nullable with no default. Since PostgreSQL 11 that is a catalog-only change: no table rewrite, no data pages touched, `ACCESS EXCLUSIVE` held for microseconds.

`NOT NULL DEFAULT ''` would rewrite every row while holding that lock, blocking all reads and writes — precisely the downtime this exercise exists to avoid. This is the most important sentence in the plan.

A partial index `WHERE first_name IS NULL` supports the backfill, shrinking to empty as it progresses, dropped at Contract.

> `CREATE INDEX CONCURRENTLY` avoids a write lock but cannot run inside a transaction, and Alembic wraps migrations in one. At this table size the plain form is instant; on a large table the index would be created outside Alembic.

### Dual write

Every insert populates all three columns. `split_guest_name()` is defined once in the service and **imported** by the backfill — two copies would eventually disagree, which is a silent data-inconsistency bug.

### Backfill

| Property | Mechanism | Why |
|---|---|---|
| Batched | 500 rows per transaction | A whole-table `UPDATE` holds row locks for the entire run |
| Resumable | `WHERE first_name IS NULL` | Kill and rerun; no checkpoint file to lose |
| Throttled | `--sleep` between batches | Yields to real traffic |
| Idempotent | migrated rows invisible to the query | Running twice is a no-op |
| Safe vs live writes | re-checks the null inside the `UPDATE` | Never clobbers a fresher dual-write |

### Switch read

`to_out()` is the only read path, so the switch is one branch in one function. The response shape never changes. It falls back to `guest_name` when the new fields are empty, which makes the flag safe to flip mid-backfill.

### Contract

Only after `--verify` reports zero remaining and zero mismatches:

```sql
ALTER TABLE reservations DROP COLUMN guest_name;
DROP INDEX idx_reservations_backfill;
```

Irreversible. Runs last, once. Take a backup first.

### Rollback

| Step | Rollback |
|---|---|
| 1 | `alembic downgrade 0001` |
| 2 | revert the commit |
| 3 | stop the script; partial backfill is harmless, dual-write keeps `guest_name` authoritative |
| 4 | `READ_NEW_NAME_FIELDS=false`, restart — no migration, no redeploy |
| 5 | none — restore from backup |

Commands: [README](../README.md#zero-downtime-migration). Verification between every step is `backfill_names.py --verify` plus `traffic.py --mode steady`; any response that is not `201` or a legitimate `409` is downtime.

---

## Seed data

| PostgreSQL | Rows | MongoDB | Docs |
|---|---:|---|---:|
| `users` | 200 | `products` | 800 |
| `restaurants` | 40 | `reviews` | 1,500 |
| `restaurant_tables` | 240 | `user_telemetry` | 5,000 |
| `reservations` | 600 | | |
| `orders` | 400 | | |
| `order_items` | ~988 | | |
| **Total** | **~2,468** | **Total** | **7,300** |

Both sit comfortably above the floor the demo needs.

Users split 5 admin / 40 staff / 155 customers so every role is exercisable. Ratios are realistic: 6 tables per restaurant, 20 menu items each, ~37 reviews each, telemetry dominating by volume as it does in production.

**Realism.** Faker for names, addresses and review prose; curated vocabularies for cuisines, cities, categories and event types — a demo full of lorem ipsum looks like a demo. Ratings weighted `[3, 5, 15, 40, 37]` across 1–5 stars, reproducing the real J-shaped distribution. Product `attributes` vary by category, so the collection genuinely holds different shapes. ~30% of reviews carry photos, ~15% of telemetry is anonymous, so optional fields are genuinely optional.

**Reproducibility.** `Faker.seed(424242)` and `random.seed(424242)` — every teammate's database is identical, so "the bug on restaurant 17" means the same thing on four machines.

**Idempotence.** PostgreSQL users get `uuid5(NS, email)`; Mongo documents get a deterministic ObjectId from `sha1(kind:n:seed)`. Mongo writes are `UpdateOne(upsert=True)` in a `bulk_write`, so a rerun upserts rather than duplicating — running the script twice five minutes before a demo is harmless.

`--reset` uses `TRUNCATE ... RESTART IDENTITY CASCADE`. Without `RESTART IDENTITY`, reseeding produces restaurant IDs starting at 41 and the `restaurant_id` values baked into Mongo documents would dangle.

One bcrypt hash is computed once and reused across all 200 seeded accounts — 200 individual hashes cost ~30 seconds for no benefit on throwaway credentials. Real signups always get their own salt.

`--verify` exits non-zero on failure, so it works as a CI gate.

---

## Performance

| Technique | Where |
|---|---|
| Projection over eager loading | `GET /products` explicit Mongo projection; `GET /restaurants` selects named columns |
| N+1 elimination | Availability fetches all conflicts in one query; `Order.items` uses `lazy="selectin"` |
| Pagination everywhere | Every list endpoint, `limit` capped at 100 |
| Compound indexes matching real queries | `(cuisine, city)`, `(restaurant_id, category)`, `(event_type, occurred_at)` |
| Partial indexes | Backfill index covers only un-migrated rows |
| Sparse indexes | `attributes.dietary`, telemetry `user_id` |
| Connection pooling | `pool_size=10`, `pool_pre_ping=True` |
| Non-blocking telemetry | Written after commit, outside the transaction |

Measurement is the next step: `EXPLAIN ANALYZE` before and after on the heaviest queries, and `.explain()` on the Mongo side.

---

## Risks

| Risk | Mitigation |
|---|---|
| `btree_gist` missing, constraint fails | Created in both `initdb` and migration 0001 with `IF NOT EXISTS` |
| Host port 5432 taken by a native PostgreSQL service | Set `POSTGRES_PORT=5433` in `.env`; committed default unchanged |
| Docker unavailable on a member's laptop | Any one member's machine can host; `.env` points elsewhere |
| Seed drifts below its floor after a refactor | `--verify` exits non-zero; it is in the definition of done |
| Merge conflict churn | Disjoint file ownership, integration day in week 7 |
| A member disengages | Weekly commits are visible; raise it in week 5, not week 11 |
| Live demo fails | Recorded backup walkthrough |
| Migration corrupts data | Every step reversible except Contract; back up before Contract |
