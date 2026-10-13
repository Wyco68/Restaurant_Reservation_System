"""Order creation - the dual-database transaction.

FLOW, AND WHY THE ORDER MATTERS
-------------------------------
    1. Validate payload                       -> 400
    2. READ products from MongoDB             -> 404 if any is missing
    3. WRITE order + items to PostgreSQL      -> single ACID transaction
    4. WRITE telemetry event to MongoDB       -> after commit, best-effort

There is no two-phase commit across PostgreSQL and MongoDB, and this code
does not pretend otherwise.

The Mongo READ happens before the PG write so an invalid product can never
produce a half-written order. The Mongo WRITE happens after the PG commit
and is non-authoritative, so its failure loses an analytics event, not
money. Step 4 is wrapped and logged precisely because it must never be able
to roll back a paid order.

Prices come from MongoDB, never from the request. A client-supplied price
is a free-lunch vulnerability.

STATUS CHANGES: PESSIMISTIC LOCKING
-----------------------------------
An order moves through a fixed set of transitions (TRANSITIONS below), and
whether a move is legal depends on the status at the moment of the write.
Read-check-write without a lock races: two staff press "ready" and
"cancelled" together, both read "preparing", both pass the check, and the
last write wins - an order the customer was told is cancelled shows ready.

advance_status() reads the status with SELECT ... FOR UPDATE. The second
request blocks on the row lock until the first commits, then reads the new
status and is refused with 409. The lock is held for one short transaction
inside one request, so it costs nothing measurable.

Why not optimistic, as for reservation edits? There the read happened in a
browser minutes earlier and no lock can span HTTP requests. Here read and
write are milliseconds apart in the same transaction, and waiting gives the
loser an exact answer ("already cancelled") instead of "retry".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, status
from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import mongo
from app.models.order import Order, OrderItem, OrderStatus
from app.models.reservation import Reservation
from app.models.restaurant import Restaurant
from app.models.user import User
from app.schemas.order import OrderCreate

log = logging.getLogger(__name__)

TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PLACED: frozenset({OrderStatus.PREPARING, OrderStatus.CANCELLED}),
    OrderStatus.PREPARING: frozenset({OrderStatus.READY, OrderStatus.CANCELLED}),
    OrderStatus.READY: frozenset({OrderStatus.COMPLETED}),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


async def create_order(session: AsyncSession, user: User, payload: OrderCreate) -> Order:
    # --- Step 1: referential checks in PostgreSQL ------------------------
    restaurant = await session.scalar(
        select(Restaurant).where(Restaurant.id == payload.restaurant_id)
    )
    if restaurant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Restaurant not found")

    if payload.reservation_id is not None:
        reservation = await session.scalar(
            select(Reservation).where(Reservation.id == payload.reservation_id)
        )
        if reservation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found")
        if reservation.user_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Reservation belongs to another user")

    # --- Step 2: READ products from MongoDB ------------------------------
    try:
        object_ids = [ObjectId(item.product_id) for item in payload.items]
    except InvalidId as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed product_id") from exc

    # Projection: only the fields needed to price the order are fetched.
    # description and attributes stay on disk.
    cursor = mongo.products().find(
        {"_id": {"$in": object_ids}},
        {"_id": 1, "name": 1, "price": 1, "currency": 1, "is_available": 1, "restaurant_id": 1},
    )
    found = {str(doc["_id"]): doc async for doc in cursor}

    missing = [i.product_id for i in payload.items if i.product_id not in found]
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown product_id: {', '.join(missing)}"
        )

    wrong_restaurant = [
        pid for pid, doc in found.items() if doc.get("restaurant_id") != payload.restaurant_id
    ]
    if wrong_restaurant:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Product does not belong to that restaurant: {', '.join(wrong_restaurant)}",
        )

    # Fail closed: a document missing the flag is treated as unavailable.
    # Defaulting to True would sell an item whenever the field is absent.
    unavailable = [pid for pid, doc in found.items() if not doc.get("is_available", False)]
    if unavailable:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Product is not currently available: {', '.join(unavailable)}",
        )

    # --- Step 3: WRITE to PostgreSQL in one transaction ------------------
    order = Order(
        user_id=user.id,
        restaurant_id=payload.restaurant_id,
        reservation_id=payload.reservation_id,
        total_amount=Decimal("0.00"),
        currency=str(found[payload.items[0].product_id].get("currency", "THB")),
    )

    total = Decimal("0.00")
    for item in payload.items:
        doc = found[item.product_id]
        # Decimal(str(...)) not Decimal(float): going through str avoids
        # inheriting binary floating point error from the BSON double.
        unit_price = Decimal(str(doc["price"])).quantize(Decimal("0.01"))
        total += unit_price * item.quantity
        order.items.append(
            OrderItem(
                product_id=item.product_id,
                # Snapshots: the receipt must survive the menu changing.
                product_name=str(doc["name"]),
                unit_price=unit_price,
                quantity=item.quantity,
                selected_attributes=item.selected_attributes,
            )
        )

    order.total_amount = total
    session.add(order)
    await session.commit()
    await session.refresh(order)

    # --- Step 4: WRITE telemetry to MongoDB (after commit, best-effort) --
    # Deliberately outside the transaction and deliberately swallowed. The
    # order is already durable; losing an analytics event must never surface
    # to the customer as a failed order.
    try:
        await mongo.user_telemetry().insert_one(
            {
                "event_type": "order_placed",
                "user_id": str(user.id),
                "occurred_at": datetime.now(UTC),
                "payload": {
                    "order_id": order.id,
                    "restaurant_id": order.restaurant_id,
                    "item_count": len(order.items),
                    "total_amount": float(order.total_amount),
                },
            }
        )
    except Exception:  # noqa: BLE001 - intentionally broad, see docstring
        log.warning("telemetry write failed for order %s", order.id, exc_info=True)

    return order


async def advance_status(session: AsyncSession, order_id: int, new: OrderStatus) -> Order:
    """Move an order to `new` if that is a legal transition from its CURRENT
    status. The status is read under a row lock; see the module docstring."""
    current = await session.scalar(
        select(Order.status).where(Order.id == order_id).with_for_update()
    )
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if new not in TRANSITIONS[current]:
        await session.rollback()   # release the lock before answering
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Order is {current.value}; it cannot move to {new.value}",
        )

    await session.execute(update(Order).where(Order.id == order_id).values(status=new))
    await session.commit()   # releases the lock
    return await session.scalar(
        select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    )


def select_my_orders(user_id, limit: int) -> Select:
    """List summary in ONE query: JOIN for the restaurant name, GROUP BY for
    the line count. Loading Order entities would add one SELECT for the
    items (selectin) and carry every line item to compute a count; one
    query per order for either would be N+1."""
    return (
        select(
            Order.id,
            Order.restaurant_id,
            Restaurant.name.label("restaurant_name"),
            Order.reservation_id,
            Order.status,
            Order.total_amount,
            Order.currency,
            Order.created_at,
            func.count(OrderItem.id).label("item_count"),
        )
        .join(Restaurant, Restaurant.id == Order.restaurant_id)
        .outerjoin(OrderItem, OrderItem.order_id == Order.id)
        .where(Order.user_id == user_id)
        .group_by(Order.id, Restaurant.name)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(limit)
    )
