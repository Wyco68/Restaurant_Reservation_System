"""TableFlow API - FastAPI application entry point.

Run:
    uvicorn app.main:app --reload

Interactive docs:
    http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import mongo, postgres
from app.routers import auth, orders, products, reservations, restaurants, users

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("tableflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Indexes are created on startup so a fresh clone is correctly indexed
    # without a manual step. create_index is idempotent.
    try:
        await mongo.ensure_indexes()
    except Exception:  # noqa: BLE001
        # Do not block startup on Mongo - /health reports the real state and
        # a dead Mongo should be visible there, not as an opaque crash loop.
        log.warning("could not create MongoDB indexes at startup", exc_info=True)
    yield
    await postgres.close()
    await mongo.close()


app = FastAPI(
    title="TableFlow API",
    description=(
        "Restaurant booking and ordering platform. "
        "PostgreSQL holds transactional state; MongoDB holds the catalogue, "
        "reviews and telemetry."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Vanilla-JS frontend is served from a different origin during development.
# Tighten this before anything reachable from a network.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(restaurants.router)
app.include_router(products.router)
app.include_router(reservations.router)
app.include_router(orders.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness + dependency check.

    Actually round-trips a query against each engine rather than reporting
    whether a client object exists. Returns 200 with per-engine booleans so
    a partial outage is visible instead of being flattened into one flag.
    """
    pg_ok = await postgres.ping()
    mongo_ok = await mongo.ping()
    return {
        "status": "ok" if (pg_ok and mongo_ok) else "degraded",
        "postgres": pg_ok,
        "mongodb": mongo_ok,
        "env": settings.app_env,
        "read_new_name_fields": settings.read_new_name_fields,
    }


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "TableFlow API", "docs": "/docs", "health": "/health"}
