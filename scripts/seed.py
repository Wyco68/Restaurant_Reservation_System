"""Seed data for both databases.

Output:
    PostgreSQL  ~2,380 rows   (200 users, 40 restaurants, 240 tables,
                               600 reservations, 400 orders, ~900 items)
    MongoDB     ~7,300 docs   (800 products, 1,500 reviews, 5,000 telemetry)

Usage:
    python scripts/seed.py            # seed (skips data that is already there)
    python scripts/seed.py --reset    # empty both databases first
    python scripts/seed.py --verify   # count only, no writes
    python scripts/seed.py --reset --reservations 20000   # CP2 demo volume

Faker uses a fixed seed, so everyone in the team gets the same data. Users
get UUIDs made from their email and MongoDB documents get fixed ObjectIds,
so running the script twice does not create duplicates.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bcrypt  # noqa: E402
from bson import ObjectId  # noqa: E402
from faker import Faker  # noqa: E402
from pymongo import MongoClient, UpdateOne  # noqa: E402
from sqlalchemy import create_engine, func, inspect, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.reservations import split_guest_name  # noqa: E402
from app.models import (  # noqa: E402
    Order,
    OrderItem,
    OrderStatus,
    Reservation,
    ReservationStatus,
    Restaurant,
    RestaurantTable,
    User,
    UserRole,
)

SEED = 424242
fake = Faker()
Faker.seed(SEED)
random.seed(SEED)

# Fixed namespace, so user UUIDs are the same on every machine.
NS = uuid.UUID("6f1c2d3e-4a5b-6c7d-8e9f-a0b1c2d3e4f5")

N_USERS = 200
N_RESTAURANTS = 40
TABLES_PER_RESTAURANT = 6
N_RESERVATIONS = 600
N_ORDERS = 400
N_PRODUCTS = 800
N_REVIEWS = 1500
N_TELEMETRY = 5000

MIN_PG_ROWS = 1000
MIN_MONGO_DOCS = 1000

CUISINES = [
    "Thai", "Japanese", "Italian", "Indian", "Chinese",
    "Korean", "Mexican", "Vietnamese", "French", "Vegan",
]
CITIES = ["Chiang Mai", "Bangkok", "Phuket", "Khon Kaen", "Hat Yai"]
CATEGORIES = ["Starter", "Main", "Dessert", "Drink", "Side"]
EVENT_TYPES = [
    "restaurant_search", "restaurant_view", "product_view",
    "reservation_attempt", "order_placed", "click",
]

# One bcrypt hash for all seeded accounts. Hashing 200 passwords takes
# about 30 seconds and these are only demo accounts. Real signups get
# their own salt.
DEMO_PASSWORD = "TableFlow123!"
DEMO_HASH = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode()


def stable_oid(kind: str, n: int) -> ObjectId:
    """Fixed ObjectId, so running the seed again updates instead of duplicating."""
    digest = hashlib.sha1(f"{kind}:{n}:{SEED}".encode()).hexdigest()[:24]
    return ObjectId(digest)


# PostgreSQL


def seed_postgres(session: Session, n_reservations: int = N_RESERVATIONS) -> dict[str, int]:
    print("  PostgreSQL...")

    # users
    users: list[User] = []
    for i in range(N_USERS):
        email = f"user{i:04d}@tableflow.io"
        users.append(
            User(
                id=uuid.uuid5(NS, email),
                email=email,
                password_hash=DEMO_HASH,
                full_name=fake.name(),
                phone=fake.msisdn()[:10],
                # First 5 admin, next 35 staff (one per restaurant), the rest customers.
                role=(
                    UserRole.ADMIN if i < 5
                    else UserRole.STAFF if i < 45
                    else UserRole.CUSTOMER
                ),
            )
        )
    session.add_all(users)
    session.flush()

    staff = [u for u in users if u.role in (UserRole.STAFF, UserRole.ADMIN)]
    customers = [u for u in users if u.role == UserRole.CUSTOMER]

    # restaurants
    restaurants: list[Restaurant] = []
    for i in range(N_RESTAURANTS):
        restaurants.append(
            Restaurant(
                owner_id=staff[i % len(staff)].id,
                name=f"{fake.last_name()} {random.choice(['Kitchen', 'House', 'Bistro', 'Table', 'Garden'])}",
                cuisine=random.choice(CUISINES),
                city=random.choice(CITIES),
                address=fake.address().replace("\n", ", "),
                price_range=random.randint(1, 4),
                avg_rating=Decimal(str(round(random.uniform(3.0, 5.0), 1))),
            )
        )
    session.add_all(restaurants)
    session.flush()

    # restaurant_tables
    tables: list[RestaurantTable] = []
    for r in restaurants:
        for t in range(TABLES_PER_RESTAURANT):
            tables.append(
                RestaurantTable(
                    restaurant_id=r.id,
                    table_number=f"T{t + 1}",
                    capacity=random.choice([2, 2, 4, 4, 6, 8]),
                )
            )
    session.add_all(tables)
    session.flush()

    tables_by_restaurant: dict[int, list[RestaurantTable]] = {}
    for t in tables:
        tables_by_restaurant.setdefault(t.restaurant_id, []).append(t)

    # reservations
    # Slots come from fixed time windows per table that do not overlap. If
    # the seed ever made an overlap, the no_double_booking constraint would
    # reject it.
    base = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    taken: set[tuple[int, int, int]] = set()  # (table_id, day, slot)
    reservations: list[Reservation] = []
    attempts = 0

    # Seeded bookings only have guest_name, so the CP2 backfill has rows to
    # migrate. On a contracted schema (0004, no guest_name) the new columns
    # are written instead.
    columns = {c["name"] for c in inspect(session.connection()).get_columns("reservations")}

    def guest_columns(name: str) -> dict[str, str]:
        if "guest_name" in columns:
            return {"guest_name": name}
        first, last = split_guest_name(name)
        return {"first_name": first, "last_name": last}

    while len(reservations) < n_reservations and attempts < n_reservations * 20:
        attempts += 1
        restaurant = random.choice(restaurants)
        table = random.choice(tables_by_restaurant[restaurant.id])
        day = random.randint(-14, 14)
        slot = random.randint(0, 5)  # 17:00, 18:30, 20:00, 21:30, 12:00, 13:30

        key = (table.id, day, slot)
        if key in taken:
            continue
        taken.add(key)

        hour, minute = [(17, 0), (18, 30), (20, 0), (21, 30), (12, 0), (13, 30)][slot]
        starts = base + timedelta(days=day, hours=hour, minutes=minute)
        reservations.append(
            Reservation(
                **guest_columns(fake.name()),
                user_id=random.choice(customers).id,
                restaurant_id=restaurant.id,
                restaurant_table_id=table.id,
                party_size=random.randint(1, min(table.capacity, 8)),
                starts_at=starts,
                ends_at=starts + timedelta(minutes=90),
                status=(
                    ReservationStatus.CANCELLED if random.random() < 0.1
                    else ReservationStatus.CONFIRMED
                ),
            )
        )

    session.add_all(reservations)
    session.flush()

    # orders and order_items
    # product_id must match the MongoDB products below, so both use
    # stable_oid().
    orders: list[Order] = []
    item_count = 0
    for i in range(N_ORDERS):
        restaurant = random.choice(restaurants)
        order = Order(
            user_id=random.choice(customers).id,
            restaurant_id=restaurant.id,
            reservation_id=None,
            status=random.choice(list(OrderStatus)),
            total_amount=Decimal("0.00"),
        )
        total = Decimal("0.00")
        for _ in range(random.randint(1, 4)):
            product_index = random.randrange(N_PRODUCTS)
            unit = Decimal(str(round(random.uniform(60, 450), 2)))
            qty = random.randint(1, 3)
            total += unit * qty
            item_count += 1
            order.items.append(
                OrderItem(
                    product_id=str(stable_oid("product", product_index)),
                    product_name=fake.word().title() + " " + random.choice(
                        ["Curry", "Noodles", "Salad", "Rice", "Soup"]
                    ),
                    unit_price=unit,
                    quantity=qty,
                    selected_attributes={"spice_level": random.randint(0, 5)},
                )
            )
        order.total_amount = total
        orders.append(order)

    session.add_all(orders)
    session.commit()

    return {
        "users": len(users),
        "restaurants": len(restaurants),
        "restaurant_tables": len(tables),
        "reservations": len(reservations),
        "orders": len(orders),
        "order_items": item_count,
    }


# MongoDB


def seed_mongo(db) -> dict[str, int]:
    print("  MongoDB...")
    now = datetime.now(UTC)

    # products
    # Attributes depend on the category, so the products have different
    # shapes.
    ops = []
    for i in range(N_PRODUCTS):
        category = random.choice(CATEGORIES)
        restaurant_id = (i % N_RESTAURANTS) + 1

        if category == "Main":
            attributes = {
                "spice_level": random.randint(0, 5),
                "protein": random.choice(["chicken", "pork", "tofu", "beef", "prawn"]),
                "dietary": random.sample(
                    ["vegetarian", "vegan", "gluten-free", "halal"], k=random.randint(0, 2)
                ),
                "portion": random.choice(["regular", "large"]),
            }
        elif category == "Drink":
            attributes = {
                "served": random.choice(["hot", "iced"]),
                "sweetness": random.choice([0, 25, 50, 75, 100]),
                "caffeine_mg": random.choice([0, 40, 80, 120]),
            }
        elif category == "Dessert":
            attributes = {
                "contains_nuts": random.choice([True, False]),
                "sweetness": random.choice(["mild", "rich"]),
                "dietary": random.sample(["vegetarian", "gluten-free"], k=random.randint(0, 1)),
            }
        else:
            attributes = {
                "size": random.choice(["S", "M", "L"]),
                "shareable": random.choice([True, False]),
            }

        doc = {
            "restaurant_id": restaurant_id,
            "name": f"{fake.word().title()} {random.choice(['Curry', 'Noodles', 'Salad', 'Rice', 'Soup', 'Tea', 'Cake'])}",
            "description": fake.sentence(nb_words=12),
            "category": category,
            "price": round(random.uniform(45, 480), 2),
            "currency": "THB",
            "is_available": random.random() > 0.08,
            "attributes": attributes,
            "created_at": now,
            "updated_at": now,
        }
        ops.append(
            UpdateOne({"_id": stable_oid("product", i)}, {"$set": doc}, upsert=True)
        )
    db["products"].bulk_write(ops, ordered=False)

    # reviews
    ops = []
    for i in range(N_REVIEWS):
        doc = {
            "restaurant_id": (i % N_RESTAURANTS) + 1,
            "user_id": str(uuid.uuid5(NS, f"user{random.randrange(45, N_USERS):04d}@tableflow.io")),
            "rating": random.choices([1, 2, 3, 4, 5], weights=[3, 5, 15, 40, 37])[0],
            "title": fake.sentence(nb_words=5).rstrip("."),
            "body": fake.paragraph(nb_sentences=3),
            "tags": random.sample(
                ["service", "value", "ambience", "portion", "speed", "cleanliness"],
                k=random.randint(1, 3),
            ),
            "metadata": {
                "visit_type": random.choice(["lunch", "dinner", "brunch"]),
                "party_size": random.randint(1, 8),
                "device": random.choice(["mobile", "desktop", "tablet"]),
            },
            "created_at": now - timedelta(days=random.randint(0, 365)),
        }
        # About 30% have photos; the field is optional.
        if random.random() < 0.3:
            doc["photos"] = [
                {"url": f"/static/reviews/{i}_{n}.jpg", "caption": fake.word()}
                for n in range(random.randint(1, 3))
            ]
        ops.append(UpdateOne({"_id": stable_oid("review", i)}, {"$set": doc}, upsert=True))
    db["reviews"].bulk_write(ops, ordered=False)

    # user_telemetry
    # The payload is different for each event_type.
    ops = []
    for i in range(N_TELEMETRY):
        event_type = random.choice(EVENT_TYPES)
        if event_type == "restaurant_search":
            payload = {
                "query": random.choice(CUISINES).lower(),
                "filters": {
                    "city": random.choice(CITIES),
                    "price_range": [1, random.randint(2, 4)],
                },
                "result_count": random.randint(0, 40),
            }
        elif event_type == "order_placed":
            payload = {
                "order_id": random.randint(1, N_ORDERS),
                "restaurant_id": random.randint(1, N_RESTAURANTS),
                "item_count": random.randint(1, 5),
                "total_amount": round(random.uniform(120, 2400), 2),
            }
        elif event_type in ("restaurant_view", "reservation_attempt"):
            payload = {
                "restaurant_id": random.randint(1, N_RESTAURANTS),
                "source": random.choice(["search", "direct", "review"]),
            }
        elif event_type == "product_view":
            payload = {"product_id": str(stable_oid("product", random.randrange(N_PRODUCTS)))}
        else:
            payload = {"element": random.choice(["book_btn", "menu_tab", "filter_city"])}

        doc = {
            "event_type": event_type,
            # About 15% are anonymous, with no user_id.
            "user_id": (
                None if random.random() < 0.15
                else str(uuid.uuid5(NS, f"user{random.randrange(45, N_USERS):04d}@tableflow.io"))
            ),
            "session_id": f"s_{random.randrange(16**8):08x}",
            "occurred_at": now - timedelta(minutes=random.randint(0, 60 * 24 * 30)),
            "payload": payload,
        }
        ops.append(UpdateOne({"_id": stable_oid("telemetry", i)}, {"$set": doc}, upsert=True))
    db["user_telemetry"].bulk_write(ops, ordered=False)

    return {
        "products": db["products"].count_documents({}),
        "reviews": db["reviews"].count_documents({}),
        "user_telemetry": db["user_telemetry"].count_documents({}),
    }


# Helpers


def reset_postgres(session: Session) -> None:
    print("  Truncating PostgreSQL...")
    # RESTART IDENTITY so ids start from 1 again; the MongoDB documents
    # refer to restaurant ids.
    session.execute(
        text(
            "TRUNCATE order_items, orders, reservations, restaurant_tables, "
            "restaurants, users RESTART IDENTITY CASCADE"
        )
    )
    session.commit()


def reset_mongo(db) -> None:
    print("  Dropping MongoDB collections...")
    for name in ("products", "reviews", "user_telemetry"):
        db[name].delete_many({})


def verify(session: Session, db) -> bool:
    pg_counts = {
        "users": session.scalar(select(func.count()).select_from(User)),
        "restaurants": session.scalar(select(func.count()).select_from(Restaurant)),
        "restaurant_tables": session.scalar(select(func.count()).select_from(RestaurantTable)),
        "reservations": session.scalar(select(func.count()).select_from(Reservation)),
        "orders": session.scalar(select(func.count()).select_from(Order)),
        "order_items": session.scalar(select(func.count()).select_from(OrderItem)),
    }
    mongo_counts = {
        name: db[name].count_documents({})
        for name in ("products", "reviews", "user_telemetry")
    }

    pg_total = sum(pg_counts.values())
    mongo_total = sum(mongo_counts.values())

    print("\n  PostgreSQL")
    for k, v in pg_counts.items():
        print(f"    {k:<20} {v:>7,}")
    print(f"    {'TOTAL':<20} {pg_total:>7,}   (minimum {MIN_PG_ROWS:,})")

    print("\n  MongoDB")
    for k, v in mongo_counts.items():
        print(f"    {k:<20} {v:>7,}")
    print(f"    {'TOTAL':<20} {mongo_total:>7,}   (minimum {MIN_MONGO_DOCS:,})")

    pg_ok = pg_total >= MIN_PG_ROWS
    mongo_ok = mongo_total >= MIN_MONGO_DOCS
    print()
    print(f"  PostgreSQL {'PASS' if pg_ok else 'FAIL'}   MongoDB {'PASS' if mongo_ok else 'FAIL'}")
    return pg_ok and mongo_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed TableFlow databases")
    parser.add_argument("--reset", action="store_true", help="wipe both databases first")
    parser.add_argument("--verify", action="store_true", help="count only, no writes")
    parser.add_argument(
        "--reservations", type=int, default=N_RESERVATIONS,
        help="legacy bookings to create; raise it so the CP2 backfill runs long enough to watch",
    )
    args = parser.parse_args()

    engine = create_engine(settings.postgres_sync_dsn)
    mongo_client = MongoClient(settings.mongo_uri)
    db = mongo_client[settings.mongo_db]

    with Session(engine) as session:
        if args.verify:
            ok = verify(session, db)
            return 0 if ok else 1

        if args.reset:
            reset_postgres(session)
            reset_mongo(db)

        existing = session.scalar(select(func.count()).select_from(User))
        if existing and not args.reset:
            print(f"  Database already holds {existing} users. "
                  f"Use --reset to reseed from scratch.")
            verify(session, db)
            return 0

        print("Seeding (this takes ~20s)...")
        pg = seed_postgres(session, args.reservations)
        mg = seed_mongo(db)

        print(f"\n  PostgreSQL rows: {sum(pg.values()):,}")
        print(f"  MongoDB docs:    {sum(mg.values()):,}")
        ok = verify(session, db)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
