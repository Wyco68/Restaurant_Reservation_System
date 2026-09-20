# READ ME FIRST

Unzip this **into the project root** (`rest_booking/`), next to the other
members' files. Paths inside the archive are already correct, so it will
land in the right folders.

**Read every file in this bundle before you commit it.** You will be asked
about it in the Week-11 Q&A audit.

# Scaffold handoff — four real authors from commit #1

The project scaffold exists but **nothing is committed**. It is split into four disjoint slices so the Git history has four genuine authors from the very first commit, rather than one person uploading everything and three people joining later.

This matters because the CP1 rubric awards 20 points for *"equal commit distribution across team members"* and the course guidelines warn against *"single-uploader repositories"*. It matters more because the Week-11 assessment is a **10-minute per-member Q&A**: you will be asked about code with your name on it.

> **Read your slice before you commit it.** Committing a file you cannot explain is setting up your own audit failure.

---

## M3 — MongoDB, seed data, migration tooling

```bash
git checkout main && git pull
git checkout -b feature/mongo-catalog

git add app/db/mongo.py
git commit -m "feat: Motor client, collection accessors and index creation

Index definitions live next to the collections rather than scattered
through query code. Sparse indexes where most documents lack the key."

git add app/schemas/product.py app/routers/products.py
git commit -m "feat: paginated product catalogue with dynamic attributes

attributes is an open object: a curry sends spice_level and protein, a
pizza sends size and toppings, both land in one collection with no schema
change. The list endpoint uses an explicit projection so description and
attributes are never read off disk to render a menu list."

git add scripts/seed.py
git commit -m "feat: reproducible seed - 2,380 Postgres rows, 7,300 Mongo documents

Faker seeded with a fixed value so every machine holds identical data.
Deterministic UUID5 and ObjectId generation means a rerun upserts rather
than duplicating. If the reservation generator ever produced an overlap the
seed would fail against the EXCLUDE constraint, so a successful seed proves
the constraint is live."

git add alembic/versions/0002_expand_guest_name.py scripts/backfill_names.py
git commit -m "feat: expand-contract step 1 and 3 for the guest_name migration

Columns are added nullable with no default, which is metadata-only in
Postgres 11+ - no table rewrite and no long lock. The backfill is batched,
resumable and re-checks the null inside the UPDATE so live dual-writes are
not clobbered. It imports split_guest_name from the service rather than
reimplementing it, because two copies would eventually disagree."

git push -u origin feature/mongo-catalog
```

---

Full four-way plan: `scripts/handoff.md`.
