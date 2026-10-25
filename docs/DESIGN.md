# Architecture & Design Decisions

Why the system is built this way. Schema reference: [DATA_MODEL.md](DATA_MODEL.md). Endpoint reference: [API_CONTRACT.md](API_CONTRACT.md). Team process: [../CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Context and goals

TableFlow is a restaurant booking and ordering platform on a hybrid database backend, with schema evolution performed without downtime.

Three problems drive the design: hybrid persistence across a relational and a document store, Expand-Contract migration under live traffic, and concurrency control. All three arise from the domain rather than being bolted on: reservations give a natural contention point (one table, one time slot, many requesters) and menu items give a naturally document-shaped entity.

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
| Frontend | Vue 3 + Vue Router, native ES modules, no build step |
| ORM | SQLAlchemy 2.x + Alembic |
| Drivers | psycopg 3, Motor |
| Containers | Docker Compose |

**On raw SQL.** Raw SQL string concatenation is banned in this codebase as an injection vector. Every query is a parameterized SQLAlchemy Core/ORM construct, with no f-string or `%`-formatted SQL anywhere. Literal SQL appears only as fixed strings with no interpolation: `docker/initdb/01_extensions.sql`; `op.execute()` in migrations for the `EXCLUDE` constraint, partial indexes and `0004`'s NOT NULL sequence, which SQLAlchemy's DDL layer does not express as cleanly; and `SET lock_timeout` in `alembic/env.py`. The rule distinguishes developer-written DDL from SQL assembled out of request data; only the latter is an injection vector.

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
frontend/          Vue 3 SPA: src/pages one module per route, vendor/ Vue + Vue Router
```

**Frontend without Node.** Node.js is forbidden by `team_project.pdf` p.3, so the client has no build step. Components are plain ES modules with string templates compiled in the browser; an import map in `index.html` resolves `vue` and `vue-router` to the browser builds in `frontend/vendor/`. They are vendored, not loaded from a CDN, so the demo runs without network (`team_project.pdf` p.9).

Routers hold HTTP concerns and simple CRUD; services hold logic that is genuinely multi-step.

**No `repositories/` layer.** The `AsyncSession` and the Motor collection handle already sit between the domain and the driver. A repository class over them is an abstraction over an abstraction. The pattern earns its place when there are multiple implementations or the ORM must not leak into the domain; neither applies here, and the tests run against real engines by design because a mock cannot prove an `EXCLUDE` constraint works. Adding it would mean four more files and no new capability.

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

- **Healthchecks** on both services, so `docker compose ps` reports *healthy* rather than merely *running*: the difference between "container started" and "database accepts connections", which is what produces a failed demo.
- **No hard-coded credentials.** Every value comes from `.env`, which is gitignored. A named bridge network keeps the services addressable by name.

Persistence is verified, not assumed:

```bash
docker compose down && docker compose up -d
python scripts/seed.py --verify     # counts unchanged
```

---

## Concurrency

There are three kinds of shared data that two users can change at the same time, and we handle each one differently.

| Resource | Race | Mechanism | The loser gets |
|---|---|---|---|
| A table for a time window | Two bookings both see the table as free | `EXCLUDE` constraint | `409` |
| An existing reservation | Two edits based on the same read | Optimistic locking with `xmin` and `If-Match` | `412` or `409` |
| An order's status | Two staff change it at once | Pessimistic locking with `SELECT ... FOR UPDATE` | `409` |

The tests and results are in [PERFORMANCE.md](PERFORMANCE.md#2-concurrency-control).

### Bookings: exclusion constraint

If twenty people book the same table for 19:00 at the same moment, only one booking may succeed. Checking in Python first and then inserting does not work, because two requests can both pass the check before either one inserts:

```python
if not has_conflict(table_id, start, end):   # T1 and T2 both read "free"
    insert_reservation(...)                  # T1 and T2 both insert
```

We let PostgreSQL enforce it instead:

```sql
EXCLUDE USING gist (
    restaurant_table_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
) WHERE (status <> 'cancelled')
```

With this constraint the database itself refuses an overlapping booking, so every code path that inserts reservations is covered, including future ones. The availability query uses the same range expression so it can use the constraint's GiST index.

We decided against using only `SELECT ... FOR UPDATE` and a check in Python, because then every place that inserts a booking has to remember the lock.

During CP3 testing we found that the constraint can deadlock. When two inserts for overlapping times run together, each one waits for the other to finish while PostgreSQL checks the constraint. After one second PostgreSQL cancels one of them, and our API returned `500`. Retrying the insert (PR #19) made it rare but did not remove it. So now `create_reservation` first locks the table's row with `FOR NO KEY UPDATE`. Bookings for the same table wait for each other, and the second one sees the first booking and gets a normal `409`. The constraint still decides who gets the slot. The downside is that bookings for the same table are handled one at a time, but each one only holds the lock for a few milliseconds.

The checks at the start of `create_reservation` (the table exists, belongs to the restaurant and is big enough) are only there to give clear `400` and `404` messages.

### Edits: optimistic, `xmin`

A customer opens a booking and changes the party size. In the meantime staff, or another browser tab, has changed the same booking. A normal `UPDATE` would overwrite that change without anyone noticing (a lost update).

We cannot hold a lock here, because the customer read the booking in the browser, possibly minutes before saving. Instead the client tells us which version it edited:

- Every reservation response includes `version` and an `ETag` header with the same value.
- `PATCH` has to send `If-Match` with that version. If it is missing we return `428`, and if it is not the current version we return `412`.
- The `UPDATE` also checks the version (`WHERE id = :id AND xmin = :version`). If another request changed the row in between, no row is updated and we return `409`.
- After `409` or `412` the client should load the booking again and retry. `DELETE` checks `If-Match` only when the client sends it.

For the version we use PostgreSQL's `xmin` system column, mapped as SQLAlchemy's `version_id_col` in `app/models/reservation.py`. PostgreSQL changes `xmin` on every update, including updates from the backfill script or `psql`, so no code has to remember to increase a version number. It also needs no migration, so the model still works on every schema from `0001` to `0004`. The only drawback is that the values are not consecutive numbers, which does not matter because clients only compare them.

### Order status: pessimistic, `FOR UPDATE`

An order moves `placed → preparing → ready → completed`, and it can be cancelled while `placed` or `preparing`. Whether a change is allowed depends on the current status. If two staff press "ready" and "cancel" at the same time without a lock, both read `preparing`, both changes look valid, and the last one wins. The customer could be told the order is cancelled while the kitchen sees it as ready.

`advance_status` in `app/services/orders.py` reads the status with `SELECT ... FOR UPDATE`. The second request waits until the first one commits, then reads the new status and gets `409` with the reason, for example `Order is cancelled; it cannot move to ready`.

We use a lock here because the read and the write happen in the same short transaction, so waiting costs almost nothing, and the second request gets a clear answer.

---

## Migration

Target: `reservations.guest_name` → `first_name` + `last_name`, with booking traffic running throughout. Procedure, commands and rollback: [MIGRATION.md](MIGRATION.md).

| Step | Schema (Alembic) | App phase (`app/migration.py`) |
|---|---|---|
| 1 Expand | `0002` adds both columns, nullable | `legacy` |
| 2 Dual write | none | `dual_write` |
| 3 Backfill | `scripts/backfill_names.py` | `dual_write` |
| 4 Switch read | none | `read_new` |
| 5 Contract | `0003` drops NOT NULL → `0004` drops the column | `new_only` between them |

Measured in three rehearsals: 25 req/s of mixed POST / GET / PATCH across all five steps, about 2,000 requests each, 0 failures, p99 under 100 ms.

### Phases change at runtime, not by redeploy

Each step that changes what the API does is a phase, switched with `PUT /api/v1/admin/migration`. A redeploy is a restart, and a restart under traffic is the downtime being demonstrated against. Every reservation write sets exactly the phase's write columns and every read loads exactly its read columns, one function each in `services/reservations.py` (`write_name`, `select_reservations`).

| Phase | Writes | Reads |
|---|---|---|
| `legacy` | `guest_name` | `guest_name` |
| `dual_write` | all three | `guest_name` |
| `read_new` | all three | `first_name`, `last_name` only |
| `new_only` | `first_name`, `last_name` | `first_name`, `last_name` |

The phase lives in process memory: one worker, one phase. Several workers would each need the change. That is the known limit, and why the demo runs one.

### Guards: out-of-order steps are refused

A phase change is checked against the live schema and data first; an unsafe one returns `409` with the reason. The same check runs at startup, so a restart after `0004` with `.env` still saying `legacy` starts in `new_only` instead of failing every request.

| Refused | Because |
|---|---|
| any phase naming a column the schema lacks | `UndefinedColumn` on every request |
| `read_new` while rows await backfill | those names would read as empty |
| `read_new` while first + last disagree with `guest_name` | names edited during a detour back to `legacy` are stale; the backfill only fills NULLs |
| `new_only` while `guest_name` is NOT NULL | every INSERT would violate it |
| `legacy` / `dual_write` once `new_only` wrote rows | those rows have no `guest_name` |

The mismatch rule is one function (`migration.name_mismatch`) used by both the guard and `backfill_names.py --verify`.

### The model never names a column the schema may lack

All three name columns exist only for part of the migration, so the mapping in `models/reservation.py` must not reference them unasked:

| Mechanism | Effect |
|---|---|
| `deferred=True` | `SELECT reservation` omits them; queries `undefer` the phase's read columns |
| `deferred_raiseload=True` | touching an unloaded one raises instead of lazy-loading a column that may not exist |
| `server_default=FetchedValue()` | an unset column is left out of the INSERT instead of being sent as NULL. No DDL; migrations own the DDL |
| `eager_defaults=False` | stops SQLAlchemy adding those columns to `INSERT … RETURNING` |

### Expand

Both columns nullable with no default. Since PostgreSQL 11 that is a catalog-only change: no table rewrite, no data pages touched, `ACCESS EXCLUSIVE` held for microseconds. `NOT NULL DEFAULT ''` would rewrite every row under that lock, blocking all reads and writes.

A partial index `WHERE first_name IS NULL` serves the backfill's cursor, shrinking to empty as it progresses; `0004` drops it.

> `CREATE INDEX CONCURRENTLY` avoids the write lock but cannot run inside a transaction, and Alembic wraps migrations in one. At this size the plain form is instant; on a large table it would be created outside Alembic.

**Lock queueing.** An `ALTER TABLE` waiting for its lock behind a slow transaction blocks every later query on the table: an outage from a migration that has not started. `alembic/env.py` sets `lock_timeout = 5s`: the migration fails cleanly and is rerun instead.

### Dual write

`split_guest_name()` is defined once in the service and **imported** by the backfill and the seed. Two copies would eventually disagree, a silent data-inconsistency bug. `PATCH /reservations/{id}` accepts `guest_name` and dual-writes it the same way as a create.

### Backfill

| Property | Mechanism | Why |
|---|---|---|
| Keyset cursor | `id > last_id ORDER BY id LIMIT 500` on the partial index | `OFFSET` rescans every skipped row on every batch |
| Short batches | one transaction per batch | a whole-table `UPDATE` holds row locks for the entire run |
| Never waits on traffic | `FOR UPDATE SKIP LOCKED`; a later pass picks skipped rows up | a row a request is updating is not worth blocking on |
| Checkpoint | prints `--start-after <id>` per batch | resume exactly; restarting without it is also safe |
| Race-safe | `UPDATE … WHERE first_name IS NULL` | never overwrites a fresher dual-write |
| One round trip per batch | executemany of a single `UPDATE` | not one statement per row |
| Throttled | `--sleep` between batches | yields to real traffic |

### Switch read

`to_out()` is the only read path. In `read_new` it builds the name from `first_name` + `last_name` and does not load `guest_name` at all. The test suite proves it by corrupting `guest_name` in the database and reading the original name back. The response shape never changes.

### Contract is two migrations

Dropping `guest_name` while the API writes it turns every booking into a `500`; the API can stop writing it only once the column accepts NULL. So: `0003` drops NOT NULL (catalog-only) → phase `new_only` → `0004` drops the column.

`0004` also:

- refuses to run while any row lacks `first_name`; the whole upgrade rolls back
- makes `first_name` / `last_name` NOT NULL without a blocking scan: `CHECK … NOT VALID` (instant) → `VALIDATE` (scans under `SHARE UPDATE EXCLUSIVE`, which does not block reads or writes) → `SET NOT NULL` (PostgreSQL 12+ trusts the valid check, no scan) → drop the check. Each in its own transaction, or the first lock would be held through the scan.

Its downgrade rebuilds `guest_name` from the new columns. That is lossless for names normalised to single spaces. It exists for rehearsals; production rollback past Contract is a backup restore.

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

**Realism.** Faker for names, addresses and review prose; curated vocabularies for cuisines, cities, categories and event types, because a demo full of lorem ipsum looks like a demo. Ratings weighted `[3, 5, 15, 40, 37]` across 1–5 stars, reproducing the real J-shaped distribution. Product `attributes` vary by category, so the collection genuinely holds different shapes. ~30% of reviews carry photos, ~15% of telemetry is anonymous, so optional fields are genuinely optional.

**Reproducibility.** `Faker.seed(424242)` and `random.seed(424242)`, so every teammate's database is identical, so "the bug on restaurant 17" means the same thing on four machines.

**Idempotence.** PostgreSQL users get `uuid5(NS, email)`; Mongo documents get a deterministic ObjectId from `sha1(kind:n:seed)`. Mongo writes are `UpdateOne(upsert=True)` in a `bulk_write`, so a rerun upserts rather than duplicating. Running the script twice five minutes before a demo is harmless.

`--reset` uses `TRUNCATE ... RESTART IDENTITY CASCADE`. Without `RESTART IDENTITY`, reseeding produces restaurant IDs starting at 41 and the `restaurant_id` values baked into Mongo documents would dangle.

One bcrypt hash is computed once and reused across all 200 seeded accounts; 200 individual hashes cost ~30 seconds for no benefit on throwaway credentials. Real signups always get their own salt.

`--verify` exits non-zero on failure, so it works as a CI gate.

---

## Performance

| Technique | Where |
|---|---|
| Column projection, no entities for read-only lists | `GET /reservations`, `GET /orders`, `GET /restaurants`, `GET /restaurants/{id}`, login, caller lookup |
| One query per list: JOIN and GROUP BY instead of a loop | Booking list joins the restaurant name; order list joins it and counts lines; availability is one query |
| Queries shaped to an existing index | Availability reuses the `no_double_booking` GiST index; product indexes put the sort field after the equality fields |
| Pagination or `limit` on every list | Capped at 100 |
| CPU-heavy work off the event loop | bcrypt in a worker thread; the pooled connection is released first |
| Partial and sparse indexes | Backfill index covers only un-migrated rows; `attributes.dietary`, telemetry `user_id` |
| Connection pooling | `pool_size=10`, `max_overflow=5`, `pool_pre_ping=True` |
| Non-blocking telemetry | Written after commit, outside the transaction |

Measurements, query plans and the load test: [PERFORMANCE.md](PERFORMANCE.md).

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
| Migration corrupts data | Guards refuse out-of-order phases; `0004` refuses unmigrated rows; `traffic.py` checks every name it reads back; back up before Contract |
| A migration step queues traffic behind its lock | `lock_timeout = 5s`; the step fails and is rerun |
