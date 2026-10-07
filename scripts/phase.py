"""Show or change the guest_name migration phase of the running API.

Wraps GET / PUT /api/v1/admin/migration so the demo needs no curl and no
token handling, on any shell.

Usage:
    python scripts/phase.py               # show phase and schema state
    python scripts/phase.py dual_write    # legacy | dual_write | read_new | new_only
"""

from __future__ import annotations

import sys

import httpx

BASE_URL = "http://localhost:8000"
ADMIN = {"email": "user0000@tableflow.io", "password": "TableFlow123!"}  # seeded admin, not a secret


def main() -> int:
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        r = client.post("/api/v1/auth/login", json=ADMIN)
        r.raise_for_status()
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

        if len(sys.argv) > 1:
            r = client.put("/api/v1/admin/migration", json={"phase": sys.argv[1]}, headers=headers)
        else:
            r = client.get("/api/v1/admin/migration", headers=headers)

    if r.status_code != 200:
        print(f"  {r.status_code}: {r.json().get('detail')}")
        return 1

    s = r.json()
    print(f"  phase              {s['phase']}")
    print(f"  writes             {', '.join(s['writes'])}")
    print(f"  reads              {', '.join(s['reads'])}")
    print(f"  guest_name         {'dropped' if not s['guest_name_present'] else 'nullable' if s['guest_name_nullable'] else 'NOT NULL'}")
    print(f"  first/last_name    {'present' if s['new_columns_present'] else 'absent'}")
    if s["awaiting_backfill"] is not None:
        print(f"  awaiting backfill  {s['awaiting_backfill']:,}")
    if s["mismatched"] is not None:
        print(f"  name mismatches    {s['mismatched']:,}")
    print(f"  allowed next       {', '.join(s['allowed_phases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
