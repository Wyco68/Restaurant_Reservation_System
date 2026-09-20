# API Reference

Base URL `http://localhost:8000`, prefix `/api/v1`. Interactive docs at `/docs`.

Schema details: [DATA_MODEL.md](DATA_MODEL.md). Design rationale: [DESIGN.md](DESIGN.md).

---

## Conventions

**Authentication.** `Authorization: Bearer <jwt>` from `POST /api/v1/auth/login`. Roles: `customer` (default), `staff`, `admin`.

**Errors.** Always `{"detail": "<message>"}`.

**Pagination.** Every list endpoint returns:

```json
{ "items": [], "page": 1, "limit": 20, "total": 812, "pages": 41 }
```

**Status codes.**

| Code | Meaning |
|---|---|
| `200` | Successful read or update |
| `201` | Created; body is the new resource |
| `400` | Shape is valid but the request is not (malformed ObjectId, cross-entity mismatch) |
| `401` | Missing, malformed or expired token |
| `403` | Authenticated but not permitted |
| `404` | Resource does not exist |
| `409` | Uniqueness or availability conflict |
| `422` | Payload failed schema validation |

`400` and `422` are both used deliberately: `422` means the shape is wrong (missing field, wrong type), `400` means the shape is fine but the request still cannot be honoured.

---

## Endpoints

| Method | Route | Database | Auth | Core |
|---|---|---|---|---|
| `POST` | `/users` | PG | — | yes |
| `GET` | `/users/{id}` | PG | bearer | yes |
| `GET` | `/users/me/profile` | PG | bearer | |
| `POST` | `/auth/login` | PG | — | |
| `GET` | `/restaurants` | PG | — | |
| `GET` | `/restaurants/{id}` | PG | — | |
| `POST` | `/restaurants` | PG | staff | |
| `GET` | `/restaurants/{id}/availability` | PG | — | |
| `GET` | `/products` | Mongo | — | yes |
| `POST` | `/products` | Mongo | staff | yes |
| `GET` | `/products/{id}` | Mongo | — | |
| `POST` | `/reservations` | PG | bearer | |
| `GET` | `/reservations/{id}` | PG | bearer | |
| `PATCH` | `/reservations/{id}` | PG | bearer | |
| `DELETE` | `/reservations/{id}` | PG | bearer | |
| `POST` | `/orders` | **PG + Mongo** | bearer | yes |
| `GET` | `/orders/{id}` | PG | bearer | |
| `PATCH` | `/orders/{id}/status` | PG | staff | |
| `GET` | `/health` | both | — | |

---

## POST /users

Register. The only write endpoint reachable without a token.

```json
{ "email": "ada@example.com", "password": "ValidPass123!", "full_name": "Ada Lovelace", "phone": "0812345678" }
```

`201`:

```json
{
  "id": "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6071",
  "email": "ada@example.com",
  "full_name": "Ada Lovelace",
  "phone": "0812345678",
  "role": "customer",
  "is_active": true,
  "created_at": "2026-09-20T09:14:22Z"
}
```

| Field | Rule |
|---|---|
| `email` | valid address, unique (case-insensitive) |
| `password` | 8–128 chars, bcrypt-hashed, never returned |
| `full_name` | 2–120 chars |
| `role` | **ignored if sent** — always `customer` |

Errors: `422` invalid payload · `409` email already registered

`role` is not accepted from the client; accepting it would be a privilege-escalation endpoint. The `409` comes from the unique index rather than a pre-check, which would race.

---

## GET /users/{id}

Response is `UserOut` as above — `password_hash` is absent from the response model, so it cannot leak.

Errors: `401` no/invalid token · `403` a customer requesting another user · `404` no such user

A customer may read only their own record; staff and admin may read any. Without that check this is a user-enumeration API.

---

## POST /auth/login

```json
{ "email": "ada@example.com", "password": "ValidPass123!" }
```

`200`:

```json
{ "access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer", "expires_in": 3600 }
```

Errors: `401` wrong credentials **or** unknown email — deliberately the same message, so the endpoint is not an account-existence oracle.

---

## GET /products

Paginated, filtered, projected catalogue.

| Parameter | Type | Default | Rule |
|---|---|---|---|
| `page` | int | 1 | ≥ 1 |
| `limit` | int | 20 | 1–100, capped |
| `restaurant_id` | int | — | > 0 |
| `category` | string | — | Starter, Main, Dessert, Drink, Side |
| `min_price`, `max_price` | decimal | — | ≥ 0 |
| `available_only` | bool | true | |
| `q` | string | — | full-text search on `name` |

```
GET /api/v1/products?restaurant_id=17&category=Main&page=2&limit=3
```

```json
{
  "items": [
    {
      "_id": "665f1a2b9c4e5d6f7a8b9c01",
      "restaurant_id": 17,
      "name": "Green Curry",
      "category": "Main",
      "price": 180.00,
      "currency": "THB",
      "is_available": true
    }
  ],
  "page": 2, "limit": 3, "total": 46, "pages": 16
}
```

**Projection.** The Mongo query passes an explicit projection listing exactly these seven fields. `description` and `attributes` are never read off disk for a list request. `tests/test_api.py::test_product_list_is_projected` asserts their absence so the optimization cannot silently regress.

Errors: `422` `limit > 100` or `page < 1`

---

## POST /products

Create a menu item with dynamic attributes. Staff/admin only.

A curry:

```json
{
  "restaurant_id": 17,
  "name": "Green Curry",
  "category": "Main",
  "price": 180.00,
  "attributes": { "spice_level": 3, "protein": "chicken", "dietary": ["gluten-free"] }
}
```

A pizza, same endpoint, structurally different attributes:

```json
{
  "restaurant_id": 17,
  "name": "Margherita",
  "category": "Main",
  "price": 320.00,
  "attributes": { "size": "L", "crust": "thin", "toppings": ["basil"], "serves": 2 }
}
```

Both land in the same collection with no schema change. `201` returns the full document including `_id`, `created_at`, `updated_at`.

| Field | Rule |
|---|---|
| `restaurant_id` | integer > 0 |
| `name` | 2–150 chars |
| `category` | enumerated |
| `price` | ≥ 0, ≤ 2 decimal places |
| `attributes` | **any JSON object** — arbitrary keys, nesting, arrays |

Errors: `401` no token · `403` customer role · `422` invalid category or negative price

---

## POST /orders

The dual-database endpoint.

```json
{
  "restaurant_id": 17,
  "reservation_id": 8812,
  "items": [
    { "product_id": "665f1a2b9c4e5d6f7a8b9c01", "quantity": 2, "selected_attributes": { "spice_level": 2 } },
    { "product_id": "665f1a2b9c4e5d6f7a8b9c02", "quantity": 1 }
  ]
}
```

The client cannot send `unit_price` or `total_amount`. A client-supplied price is a free-lunch vulnerability.

`201`:

```json
{
  "id": 4471,
  "user_id": "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6071",
  "restaurant_id": 17,
  "reservation_id": 8812,
  "status": "placed",
  "total_amount": 680.00,
  "currency": "THB",
  "created_at": "2026-09-20T12:44:10Z",
  "items": [
    {
      "id": 9001,
      "product_id": "665f1a2b9c4e5d6f7a8b9c01",
      "product_name": "Green Curry",
      "unit_price": 180.00,
      "quantity": 2,
      "selected_attributes": { "spice_level": 2 }
    }
  ]
}
```

### Flow

```
1. Validate payload                          -> 422
2. Verify restaurant / reservation in PG     -> 404 / 403
3. READ products from Mongo (projected)      -> 404 / 400 / 409
4. WRITE order + items to PG, single txn, COMMIT
5. WRITE order_placed to Mongo telemetry     -> best-effort
```

There is no two-phase commit. Correctness comes from ordering: the Mongo read precedes the PG write so an invalid product cannot produce a half-written order; the Mongo write follows the commit and is non-authoritative, wrapped and logged, so its failure loses an analytics event rather than a paid order.

Prices and names are snapshotted into `order_items` — a receipt must show what the customer actually paid.

Errors: `401` · `403` reservation belongs to another user · `404` restaurant, reservation or product not found · `400` malformed `product_id` or product from another restaurant · `409` product unavailable · `422` empty `items`

---

## POST /reservations

```json
{
  "restaurant_id": 17,
  "restaurant_table_id": 104,
  "guest_name": "Ada Lovelace",
  "party_size": 4,
  "starts_at": "2026-10-02T19:00:00+07:00",
  "ends_at": "2026-10-02T20:30:00+07:00"
}
```

`201`:

```json
{
  "id": 8812,
  "user_id": "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6071",
  "restaurant_id": 17,
  "restaurant_table_id": 104,
  "guest_name": "Ada Lovelace",
  "party_size": 4,
  "starts_at": "2026-10-02T19:00:00+07:00",
  "ends_at": "2026-10-02T20:30:00+07:00",
  "status": "confirmed",
  "created_at": "2026-09-20T09:20:00Z"
}
```

`409`:

```json
{ "detail": "That table is already booked for an overlapping time window" }
```

The `409` is produced by the `no_double_booking` exclusion constraint, not by an application availability check. Fire twenty simultaneous identical requests: exactly one commits. See [DATA_MODEL.md](DATA_MODEL.md#reservations) for the constraint and [DESIGN.md](DESIGN.md#concurrency) for why it was chosen over row locking.

| Field | Rule |
|---|---|
| `party_size` | 1–20 and ≤ the table's capacity |
| `starts_at`, `ends_at` | timezone offset required, `ends_at > starts_at`, duration ≤ 6h |
| `restaurant_table_id` | exists, active, belongs to `restaurant_id` |

Errors: `422` naive timestamp or bad window · `400` table mismatch, inactive, or capacity exceeded · `401` · `404` table not found · `409` slot taken

**Name-split read switch.** `guest_name` in the response is assembled by `app/services/reservations.py::to_out`. With `READ_NEW_NAME_FIELDS=false` it reads the legacy column; with `true` it reads `first_name` + `last_name`, falling back to the legacy value for rows the backfill has not reached. The response shape never changes, so clients cannot tell the migration happened.

---

## GET /restaurants/{id}/availability

Query: `starts_at`, `ends_at` (ISO-8601 with offset), `party_size` (1–20).

```json
[
  { "restaurant_table_id": 104, "table_number": "T4", "capacity": 4, "available": true },
  { "restaurant_table_id": 105, "table_number": "T5", "capacity": 6, "available": false }
]
```

**Advisory only.** A table shown available can be taken before the booking request arrives — an unavoidable race in any check-then-act flow. The authoritative answer is the `201`-or-`409` from `POST /reservations`.

Conflicts for all candidate tables are fetched in one query, not one per table.

Errors: `400` `ends_at <= starts_at`

---

## GET /restaurants

Query: `page`, `limit` (≤100), `city`, `cuisine`, `max_price_range` (1–4), `min_rating` (1–5).

```json
{
  "items": [
    { "id": 17, "name": "Suzuki Kitchen", "cuisine": "Japanese", "city": "Chiang Mai", "price_range": 3, "avg_rating": 4.6 }
  ],
  "page": 1, "limit": 20, "total": 40, "pages": 2
}
```

Selects six named columns rather than whole entities, so `address` and the `owner` relationship are never loaded for a result list.

---

## GET /health

```json
{ "status": "ok", "postgres": true, "mongodb": true, "env": "development", "read_new_name_fields": false }
```

Round-trips a real query against each engine. Per-engine booleans so a partial outage is visible. `status` is `degraded` when either is down.

---

## Remaining endpoints

| Route | Codes | Notes |
|---|---|---|
| `GET /users/me/profile` | `200` `401` | Saves the frontend knowing its own UUID |
| `GET /restaurants/{id}` | `200` `404` | Full record including address |
| `POST /restaurants` | `201` `401` `403` | Caller becomes `owner_id` |
| `GET /products/{id}` | `200` `400` `404` | Full document including `attributes` |
| `GET /reservations/{id}` | `200` `401` `403` `404` | |
| `PATCH /reservations/{id}` | `200` | Change `party_size` or `status` |
| `DELETE /reservations/{id}` | `200` | Sets `status: cancelled`; the constraint ignores cancelled rows, so the slot frees and history is kept |
| `GET /orders/{id}` | `200` `403` `404` | Customers see only their own |
| `PATCH /orders/{id}/status` | `200` `403` | Staff kitchen queue |
