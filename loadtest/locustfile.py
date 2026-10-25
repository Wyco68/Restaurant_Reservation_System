"""Load test (CP3 §2.3): 50 customers browse, book, edit and order for 3 minutes.

    pip install -r loadtest/requirements.txt
    locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 3m \
        --host http://localhost:8000 --csv loadtest/results/run

Exits 1 if a CP3 target is missed: p95 >= 200 ms, p99 >= 500 ms, or 1% or
more of requests failed.

Every virtual user signs in as a different seeded customer and only edits
its own bookings, at dates no other user picks. So a 409 or 412 here is a
real fault, never a collision the test arranged - every non-2xx counts as
an error.
"""

from __future__ import annotations

import itertools
import random
from datetime import UTC, datetime, timedelta

from locust import HttpUser, between, events, task

PASSWORD = "TableFlow123!"   # seed-script demo password, not a secret
CUSTOMERS = itertools.count(45)   # user0000-0044 are admin and staff
CITIES = ["Chiang Mai", "Bangkok", "Phuket", "Khon Kaen", "Hat Yai"]

P95_MS, P99_MS, MAX_ERROR_RATE = 200, 500, 0.01


class Customer(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        email = f"user{next(CUSTOMERS):04d}@tableflow.io"
        r = self.client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

        self.restaurants = [
            x["id"] for x in self.client.get(
                "/api/v1/restaurants?limit=100", name="/api/v1/restaurants"
            ).json()["items"]
        ]
        self.menus: dict[int, list[str]] = {}
        self.bookings: dict[int, int] = {}   # reservation id -> version
        # Far-future dates of this user's own: no two users share a window.
        self.next_slot = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(
            days=random.randint(2_000, 40_000)
        )

    # ---------------------------------------------------------- browsing

    @task(4)
    def search(self) -> None:
        self.client.get(
            "/api/v1/restaurants",
            params={"city": random.choice(CITIES), "page": random.randint(1, 2), "limit": 20},
            name="/api/v1/restaurants?city",
        )

    @task(4)
    def view_menu(self) -> None:
        rid = random.choice(self.restaurants)
        self.client.get(f"/api/v1/restaurants/{rid}", name="/api/v1/restaurants/{id}")
        r = self.client.get(
            "/api/v1/products", params={"restaurant_id": rid, "limit": 50},
            name="/api/v1/products?restaurant_id",
        )
        self.menus[rid] = [p["_id"] for p in r.json()["items"]]

    @task(2)
    def browse_catalogue(self) -> None:
        self.client.get(
            "/api/v1/products", params={"page": random.randint(1, 10), "limit": 20},
            name="/api/v1/products",
        )

    @task(3)
    def check_availability(self) -> None:
        self.availability(random.choice(self.restaurants), self.next_slot)

    def availability(self, rid: int, start: datetime) -> list[dict]:
        return self.client.get(
            f"/api/v1/restaurants/{rid}/availability",
            params={
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(minutes=90)).isoformat(),
                "party_size": 2,
            },
            name="/api/v1/restaurants/{id}/availability",
        ).json()

    # ---------------------------------------------------------- bookings

    @task(2)
    def book(self) -> None:
        start = self.next_slot
        self.next_slot += timedelta(hours=2)
        rid = random.choice(self.restaurants)
        free = [s for s in self.availability(rid, start) if s["available"]]
        if not free:
            return
        r = self.client.post("/api/v1/reservations", json={
            "restaurant_id": rid,
            "restaurant_table_id": free[0]["restaurant_table_id"],
            "guest_name": "Load Test",
            "party_size": 2,
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(minutes=90)).isoformat(),
        })
        if r.status_code == 201:
            self.bookings[r.json()["id"]] = r.json()["version"]

    @task(2)
    def my_bookings(self) -> None:
        self.client.get("/api/v1/reservations")

    @task(1)
    def edit_booking(self) -> None:
        if not self.bookings:
            return
        rid = random.choice(list(self.bookings))
        current = self.client.get(
            f"/api/v1/reservations/{rid}", name="/api/v1/reservations/{id}"
        ).json()
        r = self.client.patch(
            f"/api/v1/reservations/{rid}",
            json={"party_size": random.randint(1, 2)},
            headers={"If-Match": f'"{current["version"]}"'},
            name="/api/v1/reservations/{id}",
        )
        if r.status_code == 200:
            self.bookings[rid] = r.json()["version"]

    @task(1)
    def cancel_booking(self) -> None:
        if len(self.bookings) < 3:
            return
        rid, version = self.bookings.popitem()
        self.client.delete(
            f"/api/v1/reservations/{rid}",
            headers={"If-Match": f'"{version}"'},
            name="/api/v1/reservations/{id}",
        )

    # ------------------------------------------------------------ orders

    @task(1)
    def place_order(self) -> None:
        menus = {rid: dishes for rid, dishes in self.menus.items() if dishes}
        if not menus:
            return
        rid = random.choice(list(menus))
        dishes = random.sample(menus[rid], k=min(len(menus[rid]), random.randint(1, 3)))
        self.client.post("/api/v1/orders", json={
            "restaurant_id": rid,
            "items": [{"product_id": d, "quantity": random.randint(1, 2)} for d in dishes],
        })

    @task(1)
    def my_orders(self) -> None:
        self.client.get("/api/v1/orders")


@events.quitting.add_listener
def check_targets(environment, **_) -> None:
    total = environment.stats.total
    if not total.num_requests:
        return
    p95 = total.get_response_time_percentile(0.95)
    p99 = total.get_response_time_percentile(0.99)
    checks = {
        f"p95 {p95:.0f} ms < {P95_MS} ms": p95 < P95_MS,
        f"p99 {p99:.0f} ms < {P99_MS} ms": p99 < P99_MS,
        f"errors {total.fail_ratio:.2%} < {MAX_ERROR_RATE:.0%}": total.fail_ratio < MAX_ERROR_RATE,
    }
    print(f"\n{total.num_requests:,} requests, {total.total_rps:.1f} req/s average")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not all(checks.values()):
        environment.process_exit_code = 1
