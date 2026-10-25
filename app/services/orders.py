"""Orders: creating them across both databases, and changing their status.

Creating an order:
    1. check the payload                        -> 400
    2. read the products from MongoDB           -> 404 if one is missing
    3. save the order and items in PostgreSQL   -> one transaction
    4. log a telemetry event in MongoDB         -> after the commit

There is no transaction across the two databases, so the order of the steps
matters. Reading MongoDB first means a bad product never leaves a half-saved
order. The telemetry is written last and its errors are only logged, so a
MongoDB problem cannot undo a paid order. Prices always come from MongoDB,
never from the request.

Changing the status: whether a change is allowed depends on the current
status (see TRANSITIONS). If two staff change the same order at once, both
could read "preparing" and both changes would pass. advance_status() reads
the status with SELECT ... FOR UPDATE, so the second request waits for the
first one and then gets 409 if its change is no longer allowed.
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
    # Step 1: check the restaurant and reservation in PostgreSQL.
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

    # Step 2: read the products from MongoDB.
    try:
        object_ids = [ObjectId(item.product_id) for item in payload.items]
    except InvalidId as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed product_id") from exc

    # Only the fields needed for the order.
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

    # A product without the is_available field counts as unavailable.
    unavailable = [pid for pid, doc in found.items() if not doc.get("is_available", False)]
    if unavailable:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Product is not currently available: {', '.join(unavailable)}",
        )

    # Step 3: save the order and its items in one transaction.
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
        # Convert through str so the float from MongoDB becomes an exact
        # Decimal.
        unit_price = Decimal(str(doc["price"])).quantize(Decimal("0.01"))
        total += unit_price * item.quantity
        order.items.append(
            OrderItem(
                product_id=item.product_id,
                # Copy name and price so the receipt does not change later.
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

    # Step 4: telemetry. The order is already saved, so if this fails we only
    # log it and the customer still gets their order.
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
    except Exception:  # noqa: BLE001 (any error here is only logged)
        log.warning("telemetry write failed for order %s", order.id, exc_info=True)

    return order


async def advance_status(session: AsyncSession, order_id: int, new: OrderStatus) -> Order:
    """Change the order's status if the change is allowed from its current
    status. The status is read with a row lock (see the module docstring)."""
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
    """The caller's orders in one query. The restaurant name comes from a
    JOIN and the number of items from GROUP BY, so we do not load the items
    or run a query per order."""
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
