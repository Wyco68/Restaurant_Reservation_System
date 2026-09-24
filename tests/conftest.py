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
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# psycopg's async mode cannot run on Windows' default ProactorEventLoop.
# Uvicorn selects a compatible policy itself; pytest does not, so set it
# here before any loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.main import app  # noqa: E402


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
