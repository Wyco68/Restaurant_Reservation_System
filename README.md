# TableFlow

Restaurant booking and ordering platform on a dual-database backend: PostgreSQL for transactional state, MongoDB for catalogue and telemetry.

---

## Quick start

Requires Docker Desktop, Python 3.11+, Git.

```bash
git clone https://github.com/Wyco68/Restaurant_Reservation_System.git
cd Restaurant_Reservation_System
cp .env.example .env          # then set the passwords and JWT_SECRET
docker compose up -d          # wait for both to report healthy
python -m venv .venv && .venv/Scripts/activate    # bash/zsh: source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python scripts/seed.py
uvicorn app.main:app --reload
```

`POSTGRES_PASSWORD`, `MONGO_PASSWORD` and `JWT_SECRET` are required — the app refuses to start without them. Generate a secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Verify:

```bash
curl http://localhost:8000/health
# {"status":"ok","postgres":true,"mongodb":true,...}
```

| | |
|---|---|
| API | http://localhost:8000 |
| Interactive docs | http://localhost:8000/docs |
| Frontend | see below |

**If port 5432 is already in use** (a native PostgreSQL service on the host), set `POSTGRES_PORT=5433` in `.env` and re-run `docker compose up -d`. The committed default stays 5432.

### Frontend

```bash
python -m http.server 5500 --directory frontend
```

Open http://localhost:5500. Port 5500 is the origin the API's CORS config allows.

No build step and no Node: edit a file in `frontend/src/`, reload the page.

---

## Team

| Member | Role | Owns | Branch |
|---|---|---|---|
| Aung Hein Saw | PostgreSQL & Auth | Relational schema, Alembic migrations, users API, JWT + bcrypt, roles | `feature/postgres-schema` |
| Joe D' Annibelle | Reservations & Concurrency | Booking logic, availability, exclusion constraint, double-booking tests | `feature/reservations` |
| Aung Thiha | MongoDB & Data | Collections, products API, seed script, migration backfill | `feature/mongo-catalog` |
| Win Moe Aung | Infrastructure & Version Control | Docker Compose, orders dual-DB flow, frontend, documentation, Git workflow, integration, demo | `feature/infra` |

File ownership and working agreement: [CONTRIBUTING.md](CONTRIBUTING.md)

---

## Architecture

```
  Browser (Vue 3 SPA)
        │ HTTP/JSON
        ▼
  FastAPI  ── routers → services → models/schemas
        │
   ┌────┴────┐
   ▼         ▼
PostgreSQL  MongoDB
:5432       :27017

PostgreSQL          MongoDB
  users               products         dynamic attributes
  restaurants         reviews          semi-structured
  restaurant_tables   user_telemetry   high volume
  reservations  ← EXCLUDE constraint
  orders
  order_items   ┈┈┈→ soft ref to products._id
```

PostgreSQL owns anything that must be **correct**. MongoDB owns anything that must be **flexible** or cheap at **volume**.

`POST /api/v1/orders` spans both: reads prices from MongoDB, writes the order to PostgreSQL in one transaction, then writes telemetry back to MongoDB.

Rationale and trade-offs: [docs/DESIGN.md](docs/DESIGN.md)

---

## API

Base `/api/v1`. Full reference with examples: [docs/API_CONTRACT.md](docs/API_CONTRACT.md)

| Method | Route | DB | Auth |
|---|---|---|---|
| `POST` | `/users` | PG | — |
| `GET` | `/users/{id}` | PG | bearer |
| `POST` | `/auth/login` | PG | — |
| `GET` | `/restaurants` | PG | — |
| `GET` | `/restaurants/{id}/availability` | PG | — |
| `GET` | `/products` | Mongo | — |
| `POST` | `/products` | Mongo | staff |
| `POST` | `/reservations` | PG | bearer |
| `GET` | `/reservations` | PG | bearer |
| `PATCH` | `/reservations/{id}` | PG | bearer |
| `DELETE` | `/reservations/{id}` | PG | bearer |
| `POST` | `/orders` | **PG + Mongo** | bearer |
| `PATCH` | `/orders/{id}/status` | PG | staff |
| `GET` | `/health` | both | — |
| `PUT` | `/admin/migration` | PG | admin |

Status codes: `200` `201` `400` `401` `403` `404` `409` `422`

---

## Commands

### Migrations

```bash
alembic upgrade head          # apply all - ends with guest_name dropped (0004)
alembic upgrade 0001          # apply to a revision
alembic downgrade -1          # roll back one
alembic current               # show current revision
alembic history --verbose     # show history
```

### Seed

```bash
python scripts/seed.py            # seed; skips if populated
python scripts/seed.py --reset    # wipe and reseed
python scripts/seed.py --verify   # count only
python scripts/seed.py --reset --reservations 20000   # CP2 demo volume
```

Produces ~2,468 PostgreSQL rows and ~7,300 MongoDB documents.

### Tests

```bash
pytest                    # all
pytest -k concurrent      # double-booking test only
```

Integration tests — run against the Docker databases. Start and seed them first.

### Reset everything

```bash
docker compose down -v
docker compose up -d
alembic upgrade head
python scripts/seed.py
```

### Zero-downtime migration

Five Expand-Contract steps under live traffic, no restart. Proof to check after each step, rollback, troubleshooting: [docs/MIGRATION.md](docs/MIGRATION.md).

```bash
alembic downgrade 0001                                 # reset to the legacy schema
python scripts/seed.py --reset --reservations 20000
uvicorn app.main:app                                   # terminal 1: one worker, no --reload
python scripts/traffic.py --rps 25                     # terminal 2: leave running

alembic upgrade 0002                                   # 1 EXPAND
python scripts/phase.py dual_write                     # 2 DUAL WRITE
python scripts/backfill_names.py                       # 3 BACKFILL (verifies at the end)
python scripts/phase.py read_new                       # 4 SWITCH READ
alembic upgrade 0003                                   # 5 CONTRACT: guest_name nullable
python scripts/phase.py new_only                       #   stop writing guest_name
alembic upgrade 0004                                   #   drop guest_name
```

`Ctrl+C` terminal 2: every check must print `PASS` — 0 5xx, 0 rejected, 0 connection errors, 0 wrong names, p99 < 500 ms.

### Concurrency proof

```bash
python scripts/traffic.py --mode concurrent --burst 20
```

Expect exactly one `201`, nineteen `409`, one row in the database.

---

## Documentation

| Document | For |
|---|---|
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Tables, columns, constraints, indexes, collections |
| [docs/API_CONTRACT.md](docs/API_CONTRACT.md) | Every endpoint with request/response examples |
| [docs/DESIGN.md](docs/DESIGN.md) | Architecture decisions and trade-offs |
| [docs/MIGRATION.md](docs/MIGRATION.md) | Running the zero-downtime migration (CP2 demo) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branching, commits, ownership, schedule |

---

## Status

| Area | State |
|---|---|
| Containers: ports, volumes, init DDL | Done |
| PostgreSQL schema: 6 tables, PK/FK/indexes | Done |
| MongoDB: 3 collections, flexible documents | Done |
| Seed data | 2,468 rows / 7,300 documents |
| REST API | Done |
| Migration history | `0001`–`0004` |
| Expand-Contract migration run | Rehearsed twice at 25 req/s: 0 failures, p99 29–87 ms |
| Concurrency stress test (CP3) | Pending |
