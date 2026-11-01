"""TableFlow API, the FastAPI app.

Run:
    uvicorn app.main:app --reload

API docs:
    http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import cache, migration, observability
from app.config import settings
from app.db import mongo, postgres
from app.routers import admin, auth, orders, products, reservations, restaurants, users

observability.configure_logging(settings.log_level, settings.log_format)
observability.APP_INFO.labels(settings.app_version, settings.app_env).set(1)
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
    await cache.close()


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

# Added before CORS so CORS stays the outer layer and cached responses get
# its headers too.
app.add_middleware(cache.ResponseCache)

# The frontend runs on another port in development. Behind the web image
# the browser uses one origin and CORS is not involved. CORS_ORIGINS sets
# the list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Cache", "ETag"],
)

# Last added is outermost: it times and labels everything, cache hits too.
app.add_middleware(observability.ObservabilityMiddleware)

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
        # Optional: null when no cache is configured. A down Redis does not
        # degrade the API, it only makes catalogue reads slower.
        "redis": await cache.ping(),
        "env": settings.app_env,
        "version": settings.app_version,
        "name_migration_phase": migration.current().value,
    }


@app.get("/health/live", tags=["meta"], include_in_schema=False)
async def live() -> dict:
    """Liveness: the process answers. Checks nothing else, so a database
    outage never makes Kubernetes restart healthy pods."""
    return {"status": "alive"}


@app.get("/health/ready", tags=["meta"], include_in_schema=False)
async def ready(response: Response) -> dict:
    """Readiness: may this pod get traffic?

    Only PostgreSQL decides: without it no request can authenticate. A
    MongoDB outage fails the catalogue and orders but not search or
    bookings, so taking every pod out of the load balancer for it would
    turn a partial outage into a full one.
    """
    pg_ok = await postgres.ping()
    if not pg_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": pg_ok, "postgres": pg_ok, "mongodb": await mongo.ping()}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape target. Not routed by nginx or the ingress."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "TableFlow API", "docs": "/docs", "health": "/health"}
