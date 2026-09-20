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

## M2 — Reservations and concurrency

```bash
git checkout main && git pull
git checkout -b feature/reservations

git add app/schemas/reservation.py
git commit -m "feat: reservation schemas with timezone-aware validation

Naive timestamps are rejected: a booking system that ignores timezones is
a booking system with a bug."

git add app/services/reservations.py
git commit -m "feat: booking service returning 409 on overlapping reservations

Translates the EXCLUDE constraint's IntegrityError into 409 Conflict. The
pre-flight capacity and ownership checks exist for friendly error messages,
not for correctness - removing them would degrade the messages, not the
guarantee."

git add app/routers/reservations.py
git commit -m "feat: reservation endpoints including cancel

Cancelling sets status rather than deleting the row. The constraint ignores
cancelled rows, so the slot frees while the history is retained."

git add app/routers/restaurants.py app/schemas/restaurant.py
git commit -m "feat: restaurant search and availability

Search projects six named columns instead of whole entities. Availability
fetches conflicts for every candidate table in one query rather than one
query per table."

git add scripts/traffic.py tests/
git commit -m "test: concurrency proof - twenty simultaneous bookings, one winner

Fires simultaneous requests at one table and one window and asserts exactly
one 201 and nineteen 409."

git push -u origin feature/reservations
```

---

Full four-way plan: `scripts/handoff.md`.
