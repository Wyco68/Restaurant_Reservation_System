"""Simulated load generator.

Two jobs:

  steady      CP2 zero-downtime proof. Sends a continuous mix of POST, GET
              and PATCH to /api/v1/reservations at a fixed rate while the
              migration runs. Every response that carries a booking is
              checked against the name this script sent, so a migration
              step that loses or mangles a name is caught, not just one
              that returns 500. Pass criteria (CP2 §4, plus 4xx):
                  0 HTTP 5xx, 0 rejected requests, 0 connection errors,
                  0 missing or wrong names, p99 latency < 500 ms.

  concurrent  Double-booking proof. Fires N simultaneous requests for the
              SAME table and time window. Exactly one must return 201;
              every other must return 409.

Usage:
    python scripts/traffic.py                         # 20 req/s until Ctrl+C
    python scripts/traffic.py --rps 40 --duration 300
    python scripts/traffic.py --mode concurrent --burst 20
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

BASE_URL = "http://localhost:8000"
EMAIL = "user0100@tableflow.io"   # a seeded customer
PASSWORD = "TableFlow123!"          # seed-script demo password, not a secret

# Already normalised (single spaces), so every phase must echo them exactly.
NAMES = [
    "Ada Lovelace", "Grace Hopper", "Alan Turing", "Katherine Johnson",
    "Prince", "Mary Jane Watson", "Edsger Wybe Dijkstra", "Barbara Liskov",
]
P99_LIMIT_MS = 500


async def login(client: httpx.AsyncClient) -> str:
    r = await client.post(
        f"{BASE_URL}/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def pick_table(client: httpx.AsyncClient, headers: dict) -> tuple[int, int]:
    r = await client.get(f"{BASE_URL}/api/v1/restaurants?limit=1", headers=headers)
    r.raise_for_status()
    restaurant_id = r.json()["items"][0]["id"]

    start = datetime.now(UTC) + timedelta(days=200)
    r = await client.get(
        f"{BASE_URL}/api/v1/restaurants/{restaurant_id}/availability",
        params={
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(minutes=90)).isoformat(),
            "party_size": 2,
        },
        headers=headers,
    )
    r.raise_for_status()
    return restaurant_id, r.json()[0]["restaurant_table_id"]


def booking_payload(restaurant_id: int, table_id: int, start: datetime, name: str) -> dict:
    return {
        "restaurant_id": restaurant_id,
        "restaurant_table_id": table_id,
        "guest_name": name,
        "party_size": 2,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(minutes=90)).isoformat(),
    }


@dataclass
class Stats:
    codes: Counter[str] = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0         # connection failures, timeouts - no HTTP status
    bad_fields: int = 0     # 2xx with a missing or wrong guest_name
    last_problem: str = ""

    def record(self, method: str, status: int, ms: float) -> None:
        self.codes[f"{method} {status}"] += 1
        self.latencies_ms.append(ms)

    @property
    def total(self) -> int:
        return sum(self.codes.values()) + self.errors

    @property
    def server_errors(self) -> int:
        return sum(n for k, n in self.codes.items() if k.split()[1].startswith("5"))

    @property
    def rejected(self) -> int:
        # Every steady-mode request is valid and every slot distinct, so any
        # 4xx is a fault too - e.g. a NOT NULL violation surfaces as 400.
        return sum(n for k, n in self.codes.items() if k.split()[1].startswith("4"))

    def p(self, q: float) -> float:
        if len(self.latencies_ms) < 2:
            return self.latencies_ms[0] if self.latencies_ms else 0.0
        return statistics.quantiles(self.latencies_ms, n=100, method="inclusive")[q - 1]


class Steady:
    """One operation every 1/rps seconds, each in its own task.

    Scheduling on a fixed clock - not "send a batch, wait for it, sleep" -
    keeps the rate constant even when the API slows down, which is exactly
    when a migration step would be hurting it.
    """

    def __init__(self, client: httpx.AsyncClient, headers: dict, restaurant_id: int, table_id: int):
        self.client, self.headers = client, headers
        self.restaurant_id, self.table_id = restaurant_id, table_id
        self.stats = Stats()
        self.expected: dict[int, str] = {}   # reservation id -> name it must have
        self.busy: set[int] = set()          # ids with a request in flight
        self.slot = 0
        self.base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=300)

    async def call(self, method: str, path: str, **kw) -> httpx.Response | None:
        t0 = time.perf_counter()
        try:
            r = await self.client.request(method, f"{BASE_URL}{path}", headers=self.headers, **kw)
        except httpx.HTTPError as exc:
            self.stats.errors += 1
            self.stats.last_problem = f"{method} {path}: {type(exc).__name__}"
            return None
        self.stats.record(method, r.status_code, (time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            self.stats.last_problem = f"{method} {path}: {r.status_code} {r.text[:120]}"
        return r

    def check_name(self, r: httpx.Response, want: str) -> None:
        got = r.json().get("guest_name")
        if got != want:
            self.stats.bad_fields += 1
            self.stats.last_problem = f"reservation {r.json().get('id')}: name {got!r}, expected {want!r}"

    async def create(self) -> None:
        # Distinct 2-hour windows on one table: a 409 here is a real fault,
        # not the expected result of a deliberate collision.
        self.slot += 1
        start = self.base + timedelta(days=self.slot // 10, hours=(self.slot % 10) * 2)
        name = random.choice(NAMES)
        r = await self.call(
            "POST", "/api/v1/reservations",
            json=booking_payload(self.restaurant_id, self.table_id, start, name),
        )
        if r is not None and r.status_code == 201:
            self.check_name(r, name)
            self.expected[r.json()["id"]] = name

    async def read(self, rid: int) -> None:
        r = await self.call("GET", f"/api/v1/reservations/{rid}")
        if r is not None and r.status_code == 200:
            self.check_name(r, self.expected[rid])

    async def rename(self, rid: int) -> None:
        name = random.choice(NAMES)
        r = await self.call("PATCH", f"/api/v1/reservations/{rid}", json={"guest_name": name})
        if r is not None and r.status_code == 200:
            self.check_name(r, name)
            self.expected[rid] = name

    async def one(self) -> None:
        idle = [rid for rid in self.expected if rid not in self.busy]
        roll = random.random()
        if not idle or roll < 0.5:
            await self.create()
            return
        # One request per booking at a time, so a GET never races a PATCH
        # on the same row and reports a stale name as corruption.
        rid = random.choice(idle[-500:])
        self.busy.add(rid)
        try:
            await (self.read(rid) if roll < 0.85 else self.rename(rid))
        finally:
            self.busy.discard(rid)

    def status_line(self, elapsed: float) -> str:
        s = self.stats
        return (f"\r  {elapsed:6.0f}s  {s.total / max(elapsed, 1e-9):5.1f} req/s  "
                f"5xx {s.server_errors}  4xx {s.rejected}  conn-err {s.errors}  "
                f"bad-name {s.bad_fields}  "
                f"p99 {s.p(99):6.1f} ms   ")

    async def run(self, rps: int, duration: float | None) -> Stats:
        interval, tasks = 1 / rps, set()
        start = time.perf_counter()
        next_at, next_print = start, start
        try:
            while duration is None or time.perf_counter() - start < duration:
                now = time.perf_counter()
                if now >= next_at:
                    task = asyncio.create_task(self.one())
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                    next_at += interval
                if now >= next_print:
                    print(self.status_line(now - start), end="", flush=True)
                    next_print += 1
                await asyncio.sleep(max(0.0, min(next_at, next_print) - time.perf_counter()))
        except asyncio.CancelledError:
            pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        print(self.status_line(time.perf_counter() - start))
        return self.stats


def report(s: Stats) -> bool:
    print(f"\n  requests   {s.total:,}")
    for key, n in sorted(s.codes.items()):
        print(f"    {key:<12} {n:,}")
    if s.errors:
        print(f"    {'no response':<12} {s.errors:,}")
    print(f"\n  latency    p50 {s.p(50):.1f} ms   p95 {s.p(95):.1f} ms   p99 {s.p(99):.1f} ms")

    checks = {
        "0 HTTP 5xx": s.server_errors == 0,
        "0 rejected requests (4xx)": s.rejected == 0,
        "0 connection errors": s.errors == 0,
        "0 missing or wrong names": s.bad_fields == 0,
        f"p99 < {P99_LIMIT_MS} ms": s.p(99) < P99_LIMIT_MS,
    }
    print()
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if s.last_problem:
        print(f"\n  last problem: {s.last_problem}")
    ok = all(checks.values())
    print(f"\n  {'NO DOWNTIME - every request served correctly' if ok else 'DOWNTIME OR DATA LOSS DETECTED'}")
    return ok


async def steady(rps: int, duration: float | None) -> bool:
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    async with httpx.AsyncClient(timeout=10, limits=limits) as client:
        headers = {"Authorization": f"Bearer {await login(client)}"}
        restaurant_id, table_id = await pick_table(client, headers)
        print(f"  {rps} req/s against table {table_id} "
              f"({'until Ctrl+C' if duration is None else f'{duration:.0f}s'})")
        stats = await Steady(client, headers, restaurant_id, table_id).run(rps, duration)
    return report(stats)


async def concurrent(burst: int) -> bool:
    """Fire `burst` simultaneous requests at ONE table and ONE time window.

    This is the double-booking proof. Expected: exactly one 201, the rest
    409, and exactly one row in the database for that slot.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"Authorization": f"Bearer {await login(client)}"}
        restaurant_id, table_id = await pick_table(client, headers)

        start = datetime.now(UTC).replace(
            minute=0, second=0, microsecond=0
        ) + timedelta(days=500)
        payload = booking_payload(restaurant_id, table_id, start, random.choice(NAMES))

        print(f"  firing {burst} simultaneous requests for table {table_id} at {start:%Y-%m-%d %H:%M}...")
        results = await asyncio.gather(
            *[
                client.post(
                    f"{BASE_URL}/api/v1/reservations", json=payload, headers=headers
                )
                for _ in range(burst)
            ],
            return_exceptions=True,
        )

        codes = Counter(getattr(r, "status_code", 0) for r in results)
        created = codes.get(201, 0)
        conflicts = codes.get(409, 0)

        print(f"\n    201 Created   {created}")
        print(f"    409 Conflict  {conflicts}")
        for code, n in sorted(codes.items()):
            if code not in (201, 409):
                print(f"    {code or 'exception':<13} {n}   <-- UNEXPECTED")

        ok = created == 1 and created + conflicts == burst
        print(
            f"\n  {'PASS - exactly one booking won, no double-booking' if ok else 'FAIL - expected exactly one 201'}"
        )
        return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="TableFlow load generator")
    parser.add_argument("--mode", choices=["steady", "concurrent"], default="steady")
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds (steady); default runs until Ctrl+C")
    parser.add_argument("--rps", type=int, default=20, help="requests per second (steady)")
    parser.add_argument("--burst", type=int, default=20, help="simultaneous requests (concurrent)")
    args = parser.parse_args()

    try:
        if args.mode == "steady":
            ok = asyncio.run(steady(args.rps, args.duration))
        else:
            ok = asyncio.run(concurrent(args.burst))
    except KeyboardInterrupt:
        return 130
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
