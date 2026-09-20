"""SQLAlchemy models.

Imported here so Alembic autogenerate and relationship resolution both see
every table. Import order matters only in that all models must be loaded
before mappers are configured.
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
