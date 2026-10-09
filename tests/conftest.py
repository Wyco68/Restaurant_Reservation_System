"""Pytest fixtures.

These are integration tests: they run against the real Docker databases.
A mocked database proves nothing about a dual-database design, and the
things most likely to break here - the EXCLUDE constraint, the Mongo
projection, the dual-DB order flow - are exactly the things a mock hides.

Prerequisites:
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

# psycopg's async mode cannot run on Windows' default ProactorEventLoop.
# Uvicorn selects a compatible policy itself; pytest does not, so set it
# here before any loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import create_engine  # noqa: E402

from app import migration  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import postgres  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def migration_phase():
    """Run every test in the phase the app would start in, then restore it.

    ASGITransport does not run the app's lifespan, so the startup check that
    fits the phase to the schema has to happen here - otherwise the suite
    would fail against a schema that is already contracted.
    """
    before = migration.current()
    await migration.reconcile_with_schema(postgres.engine)
    yield
    migration.set_phase(before)


@pytest.fixture(scope="session")
def db():
    """Synchronous engine for asserting on columns the API does not expose."""
    engine = create_engine(settings.postgres_sync_dsn)
    yield engine
    engine.dispose()


@pytest_asyncio.fixture
async def free_slot(client: AsyncClient):
    """Return a factory for (restaurant_id, table_id, starts_at) nobody holds."""
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
    """A fresh email per test, so reruns never collide on the UNIQUE index."""
    return f"test_{uuid.uuid4().hex[:12]}@tableflow.io"


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, unique_email: str) -> dict[str, str]:
    """Register a throwaway user and return its bearer header."""
    password = "TestPassword123!"
    await client.post(
        "/api/v1/users",
        json={"email": unique_email, "password": password, "full_name": "Test User"},
    )
    r = await client.post(
        "/api/v1/auth/login", json={"email": unique_email, "password": password}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
