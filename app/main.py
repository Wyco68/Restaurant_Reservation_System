"""TableFlow API, the FastAPI app.

Run:
    uvicorn app.main:app --reload

API docs:
    http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import migration
from app.config import settings
from app.db import mongo, postgres
from app.routers import admin, auth, orders, products, reservations, restaurants, users

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("tableflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the MongoDB indexes on startup. Running it again is harmless.
    try:
        await mongo.ensure_indexes()
    except Exception:  # noqa: BLE001
        # Start anyway if MongoDB is down; /health will show it.
        log.warning("could not create MongoDB indexes at startup", exc_info=True)
    try:
        await migration.reconcile_with_schema(postgres.engine)
    except Exception:  # noqa: BLE001
        # Same for PostgreSQL.
        log.warning("could not check the schema against the migration phase", exc_info=True)
    log.info("name migration phase: %s", migration.current().value)
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

# The frontend runs on another port in development. Restrict this before
# putting the app on a network.
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
app.include_router(admin.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Health check for both databases.

    Runs a real query on each one and returns true or false for each, so you
    can see which one is down.
    """
    pg_ok = await postgres.ping()
    mongo_ok = await mongo.ping()
    return {
        "status": "ok" if (pg_ok and mongo_ok) else "degraded",
        "postgres": pg_ok,
        "mongodb": mongo_ok,
        "env": settings.app_env,
        "name_migration_phase": migration.current().value,
    }


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "TableFlow API", "docs": "/docs", "health": "/health"}
