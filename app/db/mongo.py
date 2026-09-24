"""MongoDB connection setup and collection accessors (Motor, async).

Three collections:
    products        - menu items with dynamic attributes
    reviews         - customer reviews, semi-structured
    user_telemetry  - behavioural events, HIGH VOLUME
"""

import asyncio

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

from app.config import settings

# One client per event loop. A module-level client binds to whatever loop is
# running at import time, so anything running on a different loop later - a
# test, a reload, a restarted worker - fails with "Event loop is closed".
_clients: dict[int, AsyncIOMotorClient] = {}


def get_client() -> AsyncIOMotorClient:
    loop = asyncio.get_event_loop()
    key = id(loop)
    existing = _clients.get(key)
    if existing is None:
        existing = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=5000,
            uuidRepresentation="standard",
            io_loop=loop,
        )
        _clients[key] = existing
    return existing


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[settings.mongo_db]


# --- Collection accessors -------------------------------------------------
# Named functions rather than module-level globals so the database handle is
# resolved lazily. That keeps tests able to point at a different database.


def products() -> AsyncIOMotorCollection:
    return get_db()["products"]


def reviews() -> AsyncIOMotorCollection:
    return get_db()["reviews"]


def user_telemetry() -> AsyncIOMotorCollection:
    return get_db()["user_telemetry"]


# --- Index creation -------------------------------------------------------


async def ensure_indexes() -> None:
    """Create every index the application relies on. Idempotent.

    Called on startup so a fresh clone is correctly indexed without a manual
    step. Index definitions live here, next to the collections, rather than
    being scattered through the query code.
    """
    await products().create_index([("restaurant_id", 1), ("category", 1)])
    await products().create_index([("restaurant_id", 1), ("is_available", 1)])
    await products().create_index([("name", "text")])
    await products().create_index([("price", 1)])
    await products().create_index([("attributes.dietary", 1)], sparse=True)

    await reviews().create_index([("restaurant_id", 1), ("created_at", -1)])
    await reviews().create_index([("user_id", 1)])
    await reviews().create_index([("rating", 1)])
    await reviews().create_index([("tags", 1)])

    # High-volume collection: these indexes are what keep analytics fast.
    await user_telemetry().create_index([("occurred_at", -1)])
    await user_telemetry().create_index([("event_type", 1), ("occurred_at", -1)])
    await user_telemetry().create_index([("user_id", 1), ("occurred_at", -1)], sparse=True)


async def ping() -> bool:
    try:
        await get_client().admin.command("ping")
        return True
    except Exception:
        return False


async def close() -> None:
    for c in _clients.values():
        c.close()
    _clients.clear()
