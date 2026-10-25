"""MongoDB connection and collections (Motor, async).

    products        menu items with flexible attributes
    reviews         customer reviews
    user_telemetry  user events, many writes
"""

import asyncio

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

from app.config import settings

# One client per event loop. A single global client breaks when code runs
# on another loop (tests, reloads) with "Event loop is closed".
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


# Collections are returned by functions so tests can point them at
# another database.


def products() -> AsyncIOMotorCollection:
    return get_db()["products"]


def reviews() -> AsyncIOMotorCollection:
    return get_db()["reviews"]


def user_telemetry() -> AsyncIOMotorCollection:
    return get_db()["user_telemetry"]


# Indexes


async def ensure_indexes() -> None:
    """Create all the indexes the app uses. Safe to run more than once.

    Called on startup, so a new setup is indexed without a manual step.
    """
    await products().create_index([("restaurant_id", 1), ("category", 1)])
    # Filter fields first, then the sort field, so GET /products gets the
    # documents already sorted by name and MongoDB does not sort in memory.
    await products().create_index([("restaurant_id", 1), ("is_available", 1), ("name", 1)])
    # For the catalogue without a restaurant filter. Without this index
    # MongoDB scanned every product.
    await products().create_index([("is_available", 1), ("name", 1)])
    await products().create_index([("name", "text")])
    await products().create_index([("price", 1)])
    await products().create_index([("attributes.dietary", 1)], sparse=True)

    await reviews().create_index([("restaurant_id", 1), ("created_at", -1)])
    await reviews().create_index([("user_id", 1)])
    await reviews().create_index([("rating", 1)])
    await reviews().create_index([("tags", 1)])

    # Telemetry gets many writes; these indexes are for reading it by time.
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
