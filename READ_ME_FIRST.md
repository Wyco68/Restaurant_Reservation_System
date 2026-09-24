# M2 — List my bookings (Joe)

Adds `GET /api/v1/reservations`: the signed-in user's own reservations, latest first, cancelled included. The frontend account page (`/account`) already calls it and shows "coming soon" until this is merged.

| File | Change |
|---|---|
| `app/routers/reservations.py` | New `list_my_reservations` handler (19 lines) |
| `tests/test_api.py` | 2 new tests; your concurrency test now picks an *available* table (it failed on a re-run within the same hour) |

**Read the patch before you commit it — you answer for it in the Q&A audit.**

## Apply and push

Unzip anywhere outside the repo (e.g. `Downloads/M2`), then from the project root:

```bash
git checkout main
git pull
git checkout -b feature/list-bookings
git apply --3way <path-to>/M2_list_bookings.patch
python -m pytest
git add app/routers/reservations.py tests/test_api.py
git commit -m "feat: add GET /reservations listing the caller's own bookings"
git push -u origin feature/list-bookings
```

Then open a PR to `main`. Expect 24 passed (27 once M1's profile patch is also in `main`).

**If `git apply` refuses:** copy `files/app/routers/reservations.py` over yours, then run `git apply --3way --include="tests/*" <path-to>/M2_list_bookings.patch`.

Independent of M1's profile patch — either can merge first.
