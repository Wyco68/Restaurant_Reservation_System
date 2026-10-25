"""SQLAlchemy models.

All models are imported here so Alembic and the relationships can find
every table.
"""

from app.models.base import Base
from app.models.order import Order, OrderItem, OrderStatus
from app.models.reservation import Reservation, ReservationStatus
from app.models.restaurant import Restaurant, RestaurantTable
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "Order",
    "OrderItem",
    "OrderStatus",
    "Reservation",
    "ReservationStatus",
    "Restaurant",
    "RestaurantTable",
    "User",
    "UserRole",
]
