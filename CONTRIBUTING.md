# Contributing

Working agreement for TableFlow.

> **Never commit a file you have not read and cannot explain.**

Every contributor owns their area and answers for it. Review assumes the author understands what they pushed.

---

## Branching

```
main                          always working, never committed to directly
  ├── feature/postgres-schema      Aung Hein Saw
  ├── feature/reservations         Joe D' Annibelle
  ├── feature/mongo-catalog        Aung Thiha
  └── feature/infra                Win Moe Aung
```

```bash
git checkout main
git pull
git checkout -b feature/your-area
# work, commit
git push -u origin feature/your-area
```

Open a pull request and merge it yourself. Ask a teammate to review anything touching a shared seam.

---

## Commits

Small and frequent. **Subject line only — no body, no trailers.**

```
<type>: <what changed, imperative>
```

Types: `feat` `fix` `refactor` `test` `docs` `chore`

```
feat: add EXCLUDE constraint preventing overlapping reservations
fix: return 409 instead of 500 on duplicate email
chore: docker compose with postgres and mongo
```

Not `update`, `fixes`, `asdf`, `final version FINAL`.

If the "why" matters, it belongs in [docs/DESIGN.md](docs/DESIGN.md) or a code comment — not in the commit message.

**Never commit:** `.env`, `*.db`, `*.sqlite`, passwords, API keys, `__pycache__/`, `.venv/`. `.gitignore` covers these; do not override with `git add -f`.

Committed a secret by accident? Tell the team immediately and rotate the credential. A later commit removing it does **not** remove it from history.

---

## File ownership

Disjoint by design, so merge conflicts stay rare.

| Member | Owns |
|---|---|
| **Aung Hein Saw** | `app/models/`, `app/db/postgres.py`, `app/security.py`, `app/routers/{users,auth}.py`, `alembic/` |
| **Joe D' Annibelle** | `app/services/reservations.py`, `app/routers/{reservations,restaurants}.py`, `app/schemas/reservation.py`, `scripts/traffic.py` |
| **Aung Thiha** | `app/db/mongo.py`, `app/routers/products.py`, `app/schemas/product.py`, `scripts/{seed,backfill_names}.py` |
| **Win Moe Aung** | `docker-compose.yml`, `app/{main,config}.py`, `app/services/orders.py`, `app/routers/orders.py`, `frontend/`, `README.md`, `docs/`, branch and release management |

Need a change in someone else's file? Message them first.

### Shared seams

Five places where two members' work meets. Change one without telling the other and integration breaks.

| Seam | Between | Contract |
|---|---|---|
| `product_id` | Thiha's Mongo `_id` ↔ Win Moe's `order_items` | 24-char hex string |
| `restaurant_id` | Aung Hein's PostgreSQL `id` ↔ Thiha's Mongo documents | integer, must exist in PostgreSQL |
| `CurrentUser` | Aung Hein's `security.py` ↔ all routers | annotated FastAPI dependency |
| `split_guest_name` | Joe's service ↔ Thiha's backfill | **imported, never copy-pasted** |
| Pagination envelope | Thiha's products ↔ Win Moe's frontend | `{items, page, limit, total, pages}` |

---

## Definition of done

1. Runs on a clean clone — see [README](README.md#quick-start)
2. Endpoints return the documented status codes
3. `pytest` passes
4. [docs/API_CONTRACT.md](docs/API_CONTRACT.md) matches the implementation
5. No secret in any committed file
6. Merged to `main` via a pull request you opened

Before pushing:

```bash
git status
git diff --cached
pytest
```

---

## Schedule

| Week | Aung Hein Saw | Joe D' Annibelle | Aung Thiha | Win Moe Aung |
|---|---|---|---|---|
| 1 | repo, models | reservation schemas | Mongo schema design | compose, skeleton |
| 2 | migration 0001, users API | — | collection accessors | `/health`, config |
| 3 | auth: bcrypt + JWT | EXCLUDE constraint | products endpoints | orders service |
| 4 | roles, 401/403 | booking + 409 path | seed: PostgreSQL | frontend shell |
| 5 | restaurants + tables | availability endpoint | seed: MongoDB | frontend: search, menu |
| 6 | indexes, projections | concurrency tests | seed verification | frontend: booking |
| 7 | **integration — everyone on `main`** | | | |
| 8 | **feature freeze — full run on a clean clone** | | | |
| 9 | buffer | load generator | migration 0002, backfill | README, diagram |
| 10 | polish | polish | polish | recorded walkthrough |
| 11 | **release** | | | |

Three weeks of buffer between freeze and release. That buffer is the plan, not slack.

If someone falls behind, raise it in week 5, not week 11. Redistribute early — it is survivable early and fatal late.
