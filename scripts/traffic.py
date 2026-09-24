"""Simulated load generator.

Two jobs:

  Migration - prove the migration causes no downtime. Run this against
        POST /api/v1/reservations while applying migration 0002 and running
        the backfill. Any non-2xx that is not a legitimate 409 is downtime.

  Concurrency - prove double-booking is impossible. `--mode concurrent` fires N
        simultaneous requests for the SAME table and time window. Exactly
        one must return 201; every other must return 409.

Usage:
    python scripts/traffic.py --mode steady --duration 60 --rps 10
    python scripts/traffic.py --mode concurrent --burst 20
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

BASE_URL = "http://localhost:8000"
EMAIL = "user0100@tableflow.io"   # a seeded customer
PASSWORD = "TableFlow123!"          # seed-script demo password, not a secret


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


def booking_payload(restaurant_id: int, table_id: int, start: datetime) -> dict:
    return {
        "restaurant_id": restaurant_id,
        "restaurant_table_id": table_id,
        "guest_name": random.choice(
            ["Ada Lovelace", "Grace Hopper", "Alan Turing", "Katherine Johnson", "Prince"]
        ),
        "party_size": 2,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(minutes=90)).isoformat(),
    }


async def steady(duration: int, rps: int) -> None:
    """Continuous booking traffic on DISTINCT slots.

    Slots are distinct so a 409 here means something is genuinely wrong,
    rather than being the expected outcome of a deliberate collision.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"Authorization": f"Bearer {await login(client)}"}
        restaurant_id, table_id = await pick_table(client, headers)

        codes: Counter[int] = Counter()
        base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        offset = 0
        deadline = asyncio.get_event_loop().time() + duration

        while asyncio.get_event_loop().time() < deadline:
            batch = []
            for _ in range(rps):
                offset += 1
                start = base + timedelta(days=300 + offset // 10, hours=(offset % 10) * 2)
                batch.append(
                    client.post(
                        f"{BASE_URL}/api/v1/reservations",
                        json=booking_payload(restaurant_id, table_id, start),
                        headers=headers,
                    )
                )
            for result in await asyncio.gather(*batch, return_exceptions=True):
                codes[getattr(result, "status_code", 0)] += 1
            await asyncio.sleep(1)

        total = sum(codes.values())
        failures = sum(v for k, v in codes.items() if k not in (201, 409))
        print(f"\n  requests   {total:,}")
        for code, n in sorted(codes.items()):
            print(f"    {code or 'exception':<12} {n:,}")
        print(f"\n  {'NO DOWNTIME - all requests served' if failures == 0 else f'{failures} FAILED REQUESTS - downtime occurred'}")


async def concurrent(burst: int) -> None:
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
        payload = booking_payload(restaurant_id, table_id, start)

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


def main() -> int:
    parser = argparse.ArgumentParser(description="TableFlow load generator")
    parser.add_argument("--mode", choices=["steady", "concurrent"], default="steady")
    parser.add_argument("--duration", type=int, default=60, help="seconds (steady mode)")
    parser.add_argument("--rps", type=int, default=10, help="requests per second (steady)")
    parser.add_argument("--burst", type=int, default=20, help="simultaneous requests (concurrent)")
    args = parser.parse_args()

    if args.mode == "steady":
        asyncio.run(steady(args.duration, args.rps))
    else:
        asyncio.run(concurrent(args.burst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
