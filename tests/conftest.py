"""Pytest fixtures.

The tests run against the real databases in Docker. A mock could not show
whether the EXCLUDE constraint, the MongoDB projection or the order flow
across both databases really work.

Before running:
    docker compose up -d
    alembic upgrade head
    python scripts/seed.py
"""

from __future__ import annotations

import asyncio
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# psycopg async does not work with the default event loop on Windows.
# Uvicorn changes it itself but pytest does not, so we set it here.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import create_engine  # noqa: E402

from app import migration  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import postgres  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def migration_phase():
    """Run each test in the phase the app would start in, then restore it.

    ASGITransport does not run the app's startup code, so we fit the phase
    to the schema here. Otherwise the tests would fail on a contracted
    schema.
    """
    before = migration.current()
    await migration.reconcile_with_schema(postgres.engine)
    yield
    migration.set_phase(before)


@pytest.fixture(scope="session")
def db():
    """Sync engine for checking columns the API does not return."""
    engine = create_engine(settings.postgres_sync_dsn)
    yield engine
    engine.dispose()


@pytest_asyncio.fixture
async def free_slot(client: AsyncClient):
    """Returns a function that finds a free (restaurant_id, table_id, starts_at)."""
    r = await client.get("/api/v1/restaurants?limit=1")
    items = r.json()["items"]
    if not items:
        pytest.skip("no seeded restaurants - run scripts/seed.py")
    restaurant_id = items[0]["id"]

    async def make() -> tuple[int, int, datetime]:
        start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(
            days=random.randint(2_000, 9_000), hours=random.randint(0, 23)
        )
        r = await client.get(
            f"/api/v1/restaurants/{restaurant_id}/availability",
            params={
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(minutes=90)).isoformat(),
                "party_size": 2,
            },
        )
        free = [s for s in r.json() if s["available"]]
        if not free:
            pytest.skip("no table available")
        return restaurant_id, free[0]["restaurant_table_id"], start

    return make


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def unique_email() -> str:
    """A new email for each test, so reruns do not hit the unique index."""
    return f"test_{uuid.uuid4().hex[:12]}@tableflow.io"


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, unique_email: str) -> dict[str, str]:
    """Register a test user and return its bearer header."""
    password = "TestPassword123!"
    await client.post(
        "/api/v1/users",
        json={"email": unique_email, "password": password, "full_name": "Test User"},
    )
    r = await client.post(
        "/api/v1/auth/login", json={"email": unique_email, "password": password}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
