# Performance Audit (CP3)

This report covers the three parts of Checkpoint 3: concurrency control, the query audit and the load test. The reasons behind the design are in [DESIGN.md](DESIGN.md#concurrency). Here we show how we tested them and what we measured.

## 1. Summary

| CP3 target (§2.3) | Result |
|---|---|
| 50 virtual users, read/write mix, 3 minutes | 50 users, 11,686 requests, 14 endpoints |
| Stable throughput | 65.2 req/s average, 61 to 70 req/s after ramp-up |
| p95 < 200 ms | **20 ms** |
| p99 < 500 ms | **31 ms** |
| Errors < 1% | **0 of 11,686** |

The test ran on 25 October 2026 on one laptop (16 cores). The API ran as a single uvicorn worker, with PostgreSQL 15 and MongoDB 6 in Docker. Locust ran on the same machine. The raw Locust output is in [perf/stats.csv](perf/stats.csv) and [perf/stats_history.csv](perf/stats_history.csv).

## 2. Concurrency control

We used a different mechanism for each kind of shared data. Table bookings are protected by the `EXCLUDE` constraint from CP1. Edits to a reservation use optimistic locking (Option A) with PostgreSQL's `xmin` column as the version, which the client sends back in `If-Match`. Order status changes use pessimistic locking (Option B) with `SELECT ... FOR UPDATE`.

The tests are in `tests/test_concurrency.py` and `tests/test_orders.py`:

```bash
pytest tests/test_concurrency.py tests/test_orders.py -v -s
```

| Race | Mechanism | Test | Result |
|---|---|---|---|
| 20 customers book one table for one window | `EXCLUDE` constraint | `test_twenty_customers_one_table_exactly_one_booking` | 1 × `201`, 19 × `409`, 1 row |
| The same race, 10 times in a row | `EXCLUDE` + row lock on the table | `test_repeated_booking_races_never_fail_with_500` | 1 × `201`, 19 × `409` each time |
| 20 edits of one reservation from the same version | Optimistic (`xmin`) | `test_twenty_concurrent_edits_exactly_one_wins` | 1 × `200`, 14 × `409`, 5 × `412` |
| 6 clients each add one guest and retry on conflict | Optimistic (`xmin`) | `test_read_modify_write_loses_no_update` | `party_size` goes from 2 to 8 |
| 20 staff move one order from `placed` to `preparing` | `SELECT ... FOR UPDATE` | `test_twenty_identical_changes_exactly_one_applies` | 1 × `200`, 19 × `409` |
| 10 staff press `ready`, 10 press `cancelled` | `SELECT ... FOR UPDATE` | `test_racing_ready_and_cancel_one_wins` | 1 × `200`, 19 × `409` |

Our transactions are very short (around 1 ms), so requests sent at the same time often do not actually overlap. To make the race happen every time, the edit and order tests lock the row themselves, send all 20 requests, wait one second, and then release the lock. At that point every request has already read the row.

To check that the tests really depend on the locking, we removed each mechanism and ran them again:

| Removed | Result |
|---|---|
| `version_id_col` on the reservation model | 15 of 20 edits returned `200` (14 lost updates) and the read-modify-write test lost increments |
| `.with_for_update()` in `advance_status` | 15 of 20 transitions returned `200`, and both `ready` and `cancelled` were accepted |

A client can get `412` or `409` when editing a reservation. `412` means the version it sent was already old when the request started. `409` means another request changed the row between our read and our `UPDATE`. In both cases the client should read the reservation again and retry, which is what `scripts/traffic.py` does.

The CP2 check also still passes against a running server: `python scripts/traffic.py --mode concurrent --burst 20` gives one `201` and nineteen `409`.

## 3. Query audit

```bash
pytest tests/test_query_budget.py -v
```

The test records every SQL statement the app sends during a request. For list endpoints we call the endpoint with 1 row and with 5 rows and check that the number of statements is the same, so an N+1 loop would fail the test. Other checks fail if a query starts selecting columns the response does not use.

| Endpoint | SQL per request | Columns selected | Before CP3 |
|---|---|---|---|
| `GET /restaurants` | 2 (count, page) | 6 summary columns | same |
| `GET /restaurants/{id}` | 1 | 8 response columns, no `owner_id` | whole row |
| `GET /restaurants/{id}/availability` | **1** (tables + `NOT EXISTS`) | 4 columns | 2 queries, and no index used (3.1) |
| `GET /reservations` | 2 (user, list with `JOIN restaurants`) | response columns + `restaurant_name` | the page also called `GET /restaurants/{id}` for every restaurant |
| `GET /orders` | 2 (user, list with `JOIN` + `GROUP BY`) | summary columns + item count | new endpoint |
| `GET /orders/{id}` | 3 (user, order, items) | | same |
| User lookup on every authenticated request | 1 | profile columns, no `password_hash` | whole row, including the hash |
| `POST /auth/login` | 1 | `id`, `password_hash`, `role`, `is_active` | whole row |

### 3.1 Availability query

The old check for overlapping bookings was `starts_at < :end AND ends_at > :start`. PostgreSQL had no index for this, so it read every booking of each table and filtered them. We rewrote it with the same `tstzrange` expression the `EXCLUDE` constraint uses, so PostgreSQL can use the constraint's index:

```sql
-- old query: 1.66 ms
Bitmap Heap Scan on reservations r
  Filter: ((starts_at < '...') AND (ends_at > '...'))
  Rows Removed by Filter: 70
  ->  Bitmap Index Scan on no_double_booking
        Index Cond: (restaurant_table_id = t.id)

-- new query: 0.07 ms
Index Scan using no_double_booking on reservations r
  Index Cond: ((restaurant_table_id = t.id) AND
               (tstzrange(starts_at, ends_at, '[)') && tstzrange('...', '...', '[)')))
```

We had to write `status <> 'cancelled'` as a constant in the SQL. When the status was sent as a parameter, PostgreSQL sometimes skipped the index and scanned all 21,000 bookings (3.9 ms). `test_availability_matches_the_constraint` checks the result is still correct: a booking that starts when another ends is allowed, and any overlap is not.

### 3.2 MongoDB indexes

`GET /products` filters by `restaurant_id` and `is_available` and sorts by `name`. We added indexes that end with `name`, so MongoDB returns the documents already sorted.

| Query | Before | After |
|---|---|---|
| Menu of one restaurant, sorted by name | index on `restaurant_id, is_available`, then sort in memory | index `{restaurant_id, is_available, name}`, no sort |
| Catalogue, sorted by name, 20 per page | full collection scan (800 documents), then sort | index `{is_available, name}`, reads 20 documents |

Both endpoints already returned only the summary fields; `description` and `attributes` are not read for lists.

## 4. Load test

```bash
pip install -r loadtest/requirements.txt
uvicorn app.main:app --port 8000
locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 3m \
    --host http://localhost:8000 --csv loadtest/results/run
python loadtest/charts.py loadtest/results/run docs/perf
```

At the end of the run the script checks the three targets and exits with code 1 if any of them fails.

Each virtual user logs in as a different customer from the seed data. Then it repeats these actions with a 0.5 to 1.5 s pause: search restaurants, open a restaurant and its menu, browse the catalogue, check availability, book a table, list and edit its bookings (`PATCH` with `If-Match`), cancel, place an order and list its orders. About 18% of the requests are writes. Each user books at its own far-future dates, so users never compete for the same table. Because of this, any `409` or `412` would count as an error. None happened.

![Throughput](perf/throughput.png)

![Latency](perf/latency.png)

The latency chart is cumulative from the start of the run. The 50 logins at the beginning take about 270 ms each because of bcrypt. They keep the p99 above 200 ms until they become less than 1% of all requests, at about 80 seconds.

| Endpoint | Requests | Failures | p50 ms | p95 ms | p99 ms | req/s |
|---|--:|--:|--:|--:|--:|--:|
| `POST /api/v1/auth/login` | 50 | 0 | 270 | 290 | 300 | 0.3 |
| `GET /api/v1/orders` | 418 | 0 | 13 | 18 | 21 | 2.3 |
| `POST /api/v1/orders` | 448 | 0 | 24 | 33 | 36 | 2.5 |
| `GET /api/v1/products` | 838 | 0 | 8 | 14 | 18 | 4.7 |
| `GET /api/v1/products?restaurant_id` | 1,714 | 0 | 6 | 11 | 13 | 9.6 |
| `GET /api/v1/reservations` | 837 | 0 | 12 | 17 | 19 | 4.7 |
| `POST /api/v1/reservations` | 860 | 0 | 15 | 21 | 24 | 4.8 |
| `DELETE /api/v1/reservations/{id}` | 328 | 0 | 17 | 23 | 28 | 1.8 |
| `GET /api/v1/reservations/{id}` | 385 | 0 | 10 | 14 | 17 | 2.1 |
| `PATCH /api/v1/reservations/{id}` | 385 | 0 | 12 | 20 | 22 | 2.1 |
| `GET /api/v1/restaurants` | 50 | 0 | 8 | 16 | 16 | 0.3 |
| `GET /api/v1/restaurants/{id}` | 1,715 | 0 | 8 | 12 | 15 | 9.6 |
| `GET /api/v1/restaurants/{id}/availability` | 2,060 | 0 | 9 | 14 | 16 | 11.5 |
| `GET /api/v1/restaurants?city` | 1,598 | 0 | 10 | 14 | 16 | 8.9 |
| **Aggregated** | 11,686 | 0 | 9 | 20 | 31 | 65.2 |

## 5. Problems found during testing

### 5.1 Deadlock between bookings for the same table

When 20 different customers booked the same table at the same time, about one race in six ended with one request getting a `500`. Two inserts for overlapping times were each waiting for the other one to finish while PostgreSQL checked the `EXCLUDE` constraint. PostgreSQL detected the deadlock after one second and cancelled one of them. The bug was there since CP1, but the CP2 test only used one user, so we did not see it.

| Fix | Result |
|---|---|
| None | about 1 race in 6 returned `500` |
| Retry the insert up to 5 times (PR #19) | no failures in 440 races at first, but it failed again after the database was restarted |
| Lock the table's row with `FOR NO KEY UPDATE` before inserting (PR #20) | no failures in 440 races after a restart, and no more one-second waits |

The row lock makes bookings for the same table wait for each other. The `EXCLUDE` constraint still decides which booking wins.

### 5.2 Login blocked other requests

bcrypt is slow on purpose and takes about 0.2 s per check. Our login handler ran it directly inside the async function, so the whole server waited while it ran. We measured this by sending 20 logins at the same time as other requests:

| | 20 logins take | Other requests p50 | p95 | max |
|---|--:|--:|--:|--:|
| bcrypt in the request handler | 4.1 s | 3,430 ms | 3,900 ms | 3,960 ms |
| bcrypt in a worker thread | 1.1 s | 9 ms | 230 ms | 270 ms |
| also release the database connection first | 1.1 s | 8 ms | 19 to 68 ms | 47 to 103 ms |

The changes are in `app/security.py` (`run_in_threadpool`) and `app/routers/auth.py`. The 3-minute test would have passed even without them, because logins are only 0.4% of its requests. A lot of users logging in at once would have slowed everyone down.

## 6. Limitations

- The API, both databases and Locust all ran on the same laptop, so the absolute numbers depend on that machine.
- We ran a single uvicorn worker, because the CP2 migration phase is stored per process ([DESIGN.md](DESIGN.md#phases-change-at-runtime-not-by-redeploy)).
- In the load test, users book different dates and never compete for a table. Competing bookings are tested by the concurrency tests in section 2.
