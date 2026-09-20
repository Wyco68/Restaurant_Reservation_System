# Data Model

PostgreSQL: 6 normalized tables. MongoDB: 3 collections.

Rationale for the split and cross-store trade-offs: [DESIGN.md](DESIGN.md).

---

## Placement rule

> PostgreSQL owns anything that must be **correct**. MongoDB owns anything that must be **flexible** or cheap at **volume**.

| Entity | Engine | Reason |
|---|---|---|
| `users` | PG | Identity. A duplicate user is a correctness bug. |
| `restaurants` | PG | Referenced by tables, reservations, orders. Needs FK integrity. |
| `restaurant_tables` | PG | The booked resource. Double-booking prevented by a DB constraint. |
| `reservations` | PG | Must never double-book or half-write. |
| `orders` | PG | Money. Needs ACID and `NUMERIC` precision. |
| `order_items` | PG | Written in the same transaction as its parent order, or not at all. |
| `products` | Mongo | Shape genuinely varies per item. Relational modelling means a wide sparse table or EAV. |
| `reviews` | Mongo | Semi-structured, append-mostly, never transactional. |
| `user_telemetry` | Mongo | High volume, write-heavy, schema-per-event-type. |

---

## PostgreSQL

PostgreSQL 15. Extensions installed by `docker/initdb/01_extensions.sql`:

```sql
CREATE EXTENSION IF NOT EXISTS citext;      -- case-insensitive email
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- required by the EXCLUDE constraint
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid()
```

Conventions on every table: surrogate PK, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, explicit `ON DELETE` on every FK. All timestamps are `TIMESTAMPTZ`. Money is `NUMERIC(10,2)` — never `FLOAT`, which cannot represent `0.10` exactly.

```sql
CREATE TYPE user_role          AS ENUM ('customer', 'staff', 'admin');
CREATE TYPE reservation_status AS ENUM ('pending', 'confirmed', 'cancelled');
CREATE TYPE order_status       AS ENUM ('placed', 'preparing', 'ready', 'completed', 'cancelled');
```

### users

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `UUID` | NO | **PK**, `DEFAULT gen_random_uuid()`. Non-enumerable. |
| `email` | `CITEXT` | NO | **UNIQUE**. Case-insensitive. |
| `password_hash` | `TEXT` | NO | bcrypt output |
| `full_name` | `VARCHAR(120)` | NO | |
| `phone` | `VARCHAR(32)` | YES | |
| `role` | `user_role` | NO | `DEFAULT 'customer'` |
| `is_active` | `BOOLEAN` | NO | `DEFAULT TRUE`. Soft-disable. |
| `created_at` | `TIMESTAMPTZ` | NO | |

Constraints: `UNIQUE (email)`, `CHECK (char_length(full_name) >= 2)`
Indexes: `users_pkey`, `users_email_key`, `idx_users_role (role)`

The `409` on duplicate signup comes from the unique index, not an application pre-check — a pre-check races under concurrency.

### restaurants

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `BIGSERIAL` | NO | **PK** |
| `owner_id` | `UUID` | NO | **FK users(id)** RESTRICT |
| `name` | `VARCHAR(150)` | NO | |
| `cuisine` | `VARCHAR(60)` | NO | filter facet |
| `city` | `VARCHAR(80)` | NO | filter facet |
| `address` | `TEXT` | NO | |
| `price_range` | `SMALLINT` | NO | 1–4 |
| `avg_rating` | `NUMERIC(2,1)` | YES | denormalized from Mongo `reviews` |
| `created_at` | `TIMESTAMPTZ` | NO | |

Constraints: `CHECK (price_range BETWEEN 1 AND 4)`, `CHECK (avg_rating BETWEEN 1.0 AND 5.0)` when not null
Indexes: `idx_restaurants_cuisine_city (cuisine, city)`, `idx_restaurants_owner (owner_id)`, `idx_restaurants_rating (avg_rating DESC NULLS LAST)`

### restaurant_tables

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `BIGSERIAL` | NO | **PK** |
| `restaurant_id` | `BIGINT` | NO | **FK restaurants(id)** CASCADE |
| `table_number` | `VARCHAR(10)` | NO | e.g. `T1` |
| `capacity` | `SMALLINT` | NO | |
| `is_active` | `BOOLEAN` | NO | `DEFAULT TRUE` |

Constraints: `UNIQUE (restaurant_id, table_number)`, `CHECK (capacity > 0 AND capacity <= 20)`
Indexes: `idx_tables_restaurant (restaurant_id)`

### reservations

Target of both the name-split migration and the concurrency work.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `BIGSERIAL` | NO | **PK** |
| `user_id` | `UUID` | NO | **FK users(id)** RESTRICT |
| `restaurant_id` | `BIGINT` | NO | **FK restaurants(id)** CASCADE |
| `restaurant_table_id` | `BIGINT` | NO | **FK restaurant_tables(id)** RESTRICT |
| `guest_name` | `VARCHAR(120)` | NO | **legacy — dropped at the Contract step** |
| `first_name` | `VARCHAR(60)` | YES | added by migration `0002` |
| `last_name` | `VARCHAR(60)` | YES | added by migration `0002` |
| `party_size` | `SMALLINT` | NO | |
| `starts_at` | `TIMESTAMPTZ` | NO | |
| `ends_at` | `TIMESTAMPTZ` | NO | |
| `status` | `reservation_status` | NO | `DEFAULT 'pending'` |
| `created_at` | `TIMESTAMPTZ` | NO | |

Constraints: `CHECK (party_size > 0 AND party_size <= 20)`, `CHECK (ends_at > starts_at)`, plus:

```sql
ALTER TABLE reservations
  ADD CONSTRAINT no_double_booking
  EXCLUDE USING gist (
      restaurant_table_id WITH =,
      tstzrange(starts_at, ends_at, '[)') WITH &&
  )
  WHERE (status <> 'cancelled');
```

No two non-cancelled reservations may share a table and overlap in time. The half-open range means back-to-back seatings do not clash: a booking ending 19:00 and one starting 19:00 are fine. The `WHERE` clause lets a cancellation free the slot. Concurrent inserts: one commits, the rest raise `IntegrityError`, which the service maps to `409`.

Indexes: `no_double_booking` (GiST), `idx_reservations_user (user_id, starts_at DESC)`, `idx_reservations_restaurant_time (restaurant_id, starts_at)`, `idx_reservations_backfill (id) WHERE first_name IS NULL` (migration only)

`first_name` and `last_name` are nullable with no default so adding them is metadata-only — no table rewrite, no long lock. See [DESIGN.md](DESIGN.md#migration).

### orders

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `BIGSERIAL` | NO | **PK** |
| `user_id` | `UUID` | NO | **FK users(id)** RESTRICT |
| `restaurant_id` | `BIGINT` | NO | **FK restaurants(id)** RESTRICT |
| `reservation_id` | `BIGINT` | YES | **FK reservations(id)** SET NULL |
| `status` | `order_status` | NO | `DEFAULT 'placed'` |
| `total_amount` | `NUMERIC(10,2)` | NO | computed server-side |
| `currency` | `CHAR(3)` | NO | `DEFAULT 'THB'` |
| `created_at` | `TIMESTAMPTZ` | NO | |

Constraints: `CHECK (total_amount >= 0)`
Indexes: `idx_orders_user (user_id, created_at DESC)`, `idx_orders_restaurant_status (restaurant_id, status)`, `idx_orders_reservation (reservation_id)`

### order_items

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `BIGSERIAL` | NO | **PK** |
| `order_id` | `BIGINT` | NO | **FK orders(id)** CASCADE |
| `product_id` | `VARCHAR(24)` | NO | **soft reference to Mongo `products._id`** — no FK possible |
| `product_name` | `VARCHAR(150)` | NO | **snapshot** at purchase |
| `unit_price` | `NUMERIC(10,2)` | NO | **snapshot** at purchase |
| `quantity` | `SMALLINT` | NO | |
| `selected_attributes` | `JSONB` | YES | chosen options |

Constraints: `CHECK (quantity > 0 AND quantity <= 50)`, `CHECK (unit_price >= 0)`
Indexes: `idx_order_items_order (order_id)`, `idx_order_items_product (product_id)`

### Relationships

```
users 1──┬──N restaurants        (owner_id,  RESTRICT)
         ├──N reservations       (user_id,   RESTRICT)
         └──N orders             (user_id,   RESTRICT)

restaurants 1──┬──N restaurant_tables  (CASCADE)
               ├──N reservations       (CASCADE)
               └──N orders             (RESTRICT)

restaurant_tables 1──N reservations    (RESTRICT)
reservations      1──N orders          (SET NULL)
orders            1──N order_items     (CASCADE)

order_items.product_id ┈┈┈> mongo.products._id   (soft reference)
```

CASCADE where the child is meaningless alone; RESTRICT where deletion would destroy financial history; SET NULL for the one optional link.

---

## MongoDB

Database `tableflow`. Indexes created on startup by `app/db/mongo.py::ensure_indexes`.

### products

Backs `GET /api/v1/products` and `POST /api/v1/products`. Satisfies the dynamic-attributes requirement.

```json
{
  "_id": "665f1a2b9c4e5d6f7a8b9c01",
  "restaurant_id": 17,
  "name": "Green Curry",
  "category": "Main",
  "price": 180.00,
  "currency": "THB",
  "is_available": true,
  "attributes": { "spice_level": 3, "protein": "chicken", "dietary": ["gluten-free"] },
  "created_at": "2026-03-04T08:12:00Z",
  "updated_at": "2026-03-04T08:12:00Z"
}
```

A second document in the **same collection**, structurally different — the design, not an inconsistency:

```json
{
  "_id": "665f1a2b9c4e5d6f7a8b9c02",
  "restaurant_id": 17,
  "name": "Margherita Pizza",
  "category": "Main",
  "price": 320.00,
  "attributes": { "size": "L", "crust": "thin", "toppings": ["basil"], "serves": 2 }
}
```

| Field | Required | Validated as |
|---|---|---|
| `restaurant_id` | yes | integer, must exist in PostgreSQL |
| `name` | yes | 2–150 chars |
| `category` | yes | Starter, Main, Dessert, Drink, Side |
| `price` | yes | ≥ 0, 2 decimal places |
| `currency` | yes | ISO 4217, default THB |
| `is_available` | yes | boolean |
| **`attributes`** | no | **free-form object** |

`attributes` is validated as "an object" and nothing more. Never used for pricing or authorization — only top-level `price` is authoritative.

Indexes: `{restaurant_id, category}`, `{restaurant_id, is_available}`, `{name: text}`, `{price}`, `{attributes.dietary}` sparse

Volume: catalogue-scale, ~800 seeded.

### reviews

```json
{
  "_id": "665f1b3c9c4e5d6f7a8b9d01",
  "restaurant_id": 17,
  "user_id": "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6071",
  "rating": 5,
  "title": "Best green curry in town",
  "body": "Table was ready on time.",
  "tags": ["service", "value"],
  "photos": [{ "url": "/static/reviews/1.jpg", "caption": "the curry" }],
  "metadata": { "visit_type": "dinner", "party_size": 4, "device": "mobile" },
  "created_at": "2026-03-09T13:40:00Z"
}
```

Required: `restaurant_id`, `user_id`, `rating` (1–5), `created_at`.
Flexible: `tags[]` open vocabulary, `photos[]`, `metadata{}` — open so fields can be added without a migration.

Indexes: `{restaurant_id, created_at: -1}`, `{user_id}`, `{rating}`, `{tags}` multikey

Volume: moderate, ~1,500 seeded.

### user_telemetry

**High volume.** Write-heavy, append-only, one payload shape per `event_type`.

```json
{
  "_id": "665f1c4d9c4e5d6f7a8b9e01",
  "event_type": "restaurant_search",
  "user_id": "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6071",
  "session_id": "s_9f8e7d6c",
  "occurred_at": "2026-03-09T12:01:33Z",
  "payload": { "query": "thai", "filters": { "city": "Chiang Mai" }, "result_count": 18 }
}
```

Required: `event_type`, `occurred_at`.
Flexible: `payload{}` differs per event type by design. `user_id` is `null` for anonymous visitors — the case a `NOT NULL` relational column would have made awkward.

`event_type` is one of: `restaurant_search`, `restaurant_view`, `product_view`, `reservation_attempt`, `order_placed`, `click`.

Indexes: `{occurred_at: -1}`, `{event_type, occurred_at: -1}`, `{user_id, occurred_at: -1}` sparse, optional TTL 90 days

Volume: high, ~5,000 seeded. The only collection expected to grow without bound.

---
