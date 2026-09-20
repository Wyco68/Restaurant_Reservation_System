"""MongoDB connection setup and collection accessors (Motor, async).

Three collections (CP1 §2.2 requires a minimum of two):
    products        - menu items with dynamic attributes
    reviews         - customer reviews, semi-structured
    user_telemetry  - behavioural events, HIGH VOLUME
"""

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

from app.config import settings

client: AsyncIOMotorClient = AsyncIOMotorClient(
    settings.mongo_uri,
    serverSelectionTimeoutMS=5000,
    uuidRepresentation="standard",
)


def get_db() -> AsyncIOMotorDatabase:
    return client[settings.mongo_db]


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

    # High-volume collection: these indexes are what keep CP3 analytics fast.
    await user_telemetry().create_index([("occurred_at", -1)])
    await user_telemetry().create_index([("event_type", 1), ("occurred_at", -1)])
    await user_telemetry().create_index([("user_id", 1), ("occurred_at", -1)], sparse=True)


async def ping() -> bool:
    try:
        await client.admin.command("ping")
        return True
    except Exception:
        return False


async def close() -> None:
    client.close()
