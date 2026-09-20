"""Orders router - the dual-database endpoint.

    POST /api/v1/orders   -> 201, PostgreSQL + MongoDB

The flow and its failure semantics live in app/services/orders.py.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_session
from app.models.order import Order
from app.models.user import UserRole
from app.schemas.order import OrderCreate, OrderOut, OrderStatusUpdate
from app.security import CurrentUser, require_role
from app.services import orders as order_service

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Validation failed or product/restaurant mismatch"},
        401: {"description": "Missing or invalid token"},
        404: {"description": "Restaurant, reservation or product not found"},
        409: {"description": "A product is unavailable"},
    },
)
async def create_order(payload: OrderCreate, session: Session, current: CurrentUser) -> Order:
    """Create an order.

    Reads product pricing from MongoDB, writes the order to PostgreSQL in a
    single transaction, then records a telemetry event in MongoDB. Prices
    are never taken from the request body.
    """
    return await order_service.create_order(session, current, payload)


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: int, session: Session, current: CurrentUser) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if current.role == UserRole.CUSTOMER and order.user_id != current.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your order")
    return order


@router.patch(
    "/{order_id}/status",
    response_model=OrderOut,
    dependencies=[Depends(require_role(UserRole.STAFF, UserRole.ADMIN))],
)
async def update_status(
    order_id: int, payload: OrderStatusUpdate, session: Session
) -> Order:
    """Advance an order through the kitchen. Staff/admin only."""
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    order.status = payload.status
    await session.commit()
    await session.refresh(order)
    return order
