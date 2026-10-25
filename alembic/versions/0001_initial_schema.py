"""Initial schema: 6 tables with constraints and indexes.

Revision ID: 0001
Revises:
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # docker/initdb/01_extensions.sql also creates these for a new container.
    # IF NOT EXISTS here makes the migration work on other databases too.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    user_role = postgresql.ENUM(
        "customer", "staff", "admin", name="user_role", create_type=False
    )
    reservation_status = postgresql.ENUM(
        "pending", "confirmed", "cancelled", name="reservation_status", create_type=False
    )
    order_status = postgresql.ENUM(
        "placed", "preparing", "ready", "completed", "cancelled",
        name="order_status", create_type=False,
    )
    user_role.create(op.get_bind(), checkfirst=True)
    reservation_status.create(op.get_bind(), checkfirst=True)
    order_status.create(op.get_bind(), checkfirst=True)

    # users
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("role", user_role, server_default="customer", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("char_length(full_name) >= 2", name="ck_users_name_len"),
    )
    op.create_index("idx_users_role", "users", ["role"])

    # restaurants
    op.create_table(
        "restaurants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("cuisine", sa.String(60), nullable=False),
        sa.Column("city", sa.String(80), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("price_range", sa.SmallInteger(), nullable=False),
        sa.Column("avg_rating", sa.Numeric(2, 1), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], ondelete="RESTRICT", name="fk_restaurants_owner"
        ),
        sa.CheckConstraint("price_range BETWEEN 1 AND 4", name="ck_restaurants_price_range"),
        sa.CheckConstraint(
            "avg_rating IS NULL OR (avg_rating >= 1.0 AND avg_rating <= 5.0)",
            name="ck_restaurants_avg_rating",
        ),
    )
    op.create_index("idx_restaurants_cuisine_city", "restaurants", ["cuisine", "city"])
    op.create_index("idx_restaurants_owner", "restaurants", ["owner_id"])
    op.execute(
        "CREATE INDEX idx_restaurants_rating ON restaurants (avg_rating DESC NULLS LAST)"
    )

    # restaurant_tables
    op.create_table(
        "restaurant_tables",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("table_number", sa.String(10), nullable=False),
        sa.Column("capacity", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["restaurant_id"], ["restaurants.id"], ondelete="CASCADE",
            name="fk_tables_restaurant",
        ),
        sa.UniqueConstraint(
            "restaurant_id", "table_number", name="uq_tables_restaurant_number"
        ),
        sa.CheckConstraint("capacity > 0 AND capacity <= 20", name="ck_tables_capacity"),
    )
    op.create_index("idx_tables_restaurant", "restaurant_tables", ["restaurant_id"])

    # reservations
    op.create_table(
        "reservations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("restaurant_table_id", sa.BigInteger(), nullable=False),
        # Only guest_name at first. first_name and last_name are added by 0002
        # (Expand), so the CP2 migration has real work to do.
        sa.Column("guest_name", sa.String(120), nullable=False),
        sa.Column("party_size", sa.SmallInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", reservation_status, server_default="pending", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_reservations_user"
        ),
        sa.ForeignKeyConstraint(
            ["restaurant_id"], ["restaurants.id"], ondelete="CASCADE",
            name="fk_reservations_restaurant",
        ),
        sa.ForeignKeyConstraint(
            ["restaurant_table_id"], ["restaurant_tables.id"], ondelete="RESTRICT",
            name="fk_reservations_table",
        ),
        sa.CheckConstraint("party_size > 0 AND party_size <= 20", name="ck_reservations_party"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_reservations_time_order"),
    )
    op.execute(
        "CREATE INDEX idx_reservations_user ON reservations (user_id, starts_at DESC)"
    )
    op.create_index(
        "idx_reservations_restaurant_time", "reservations", ["restaurant_id", "starts_at"]
    )

    # Double-booking constraint: two reservations that are not cancelled
    # cannot use the same table at overlapping times. It needs btree_gist,
    # because it mixes = and && in one GiST index.
    #
    # '[)' means a booking that ends at 19:00 and one that starts at 19:00 do
    # not overlap. Cancelled bookings are ignored, so cancelling frees the
    # slot. When two overlapping inserts arrive together, one commits and the
    # other gets an IntegrityError, which the API returns as 409.
    op.execute(
        """
        ALTER TABLE reservations
          ADD CONSTRAINT no_double_booking
          EXCLUDE USING gist (
              restaurant_table_id WITH =,
              tstzrange(starts_at, ends_at, '[)') WITH &&
          )
          WHERE (status <> 'cancelled')
        """
    )

    # orders
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("restaurant_id", sa.BigInteger(), nullable=False),
        sa.Column("reservation_id", sa.BigInteger(), nullable=True),
        sa.Column("status", order_status, server_default="placed", nullable=False),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default=sa.text("'THB'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_orders_user"
        ),
        sa.ForeignKeyConstraint(
            ["restaurant_id"], ["restaurants.id"], ondelete="RESTRICT",
            name="fk_orders_restaurant",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["reservations.id"], ondelete="SET NULL",
            name="fk_orders_reservation",
        ),
        sa.CheckConstraint("total_amount >= 0", name="ck_orders_total_nonneg"),
    )
    op.execute("CREATE INDEX idx_orders_user ON orders (user_id, created_at DESC)")
    op.create_index("idx_orders_restaurant_status", "orders", ["restaurant_id", "status"])
    op.create_index("idx_orders_reservation", "orders", ["reservation_id"])

    # order_items
    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        # MongoDB products._id, so there is no foreign key. The API checks the
        # product in MongoDB before saving, and copies name and price below.
        sa.Column("product_id", sa.String(24), nullable=False),
        sa.Column("product_name", sa.String(150), nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("quantity", sa.SmallInteger(), nullable=False),
        sa.Column("selected_attributes", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="CASCADE", name="fk_order_items_order"
        ),
        sa.CheckConstraint("quantity > 0 AND quantity <= 50", name="ck_order_items_qty"),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_items_price_nonneg"),
    )
    op.create_index("idx_order_items_order", "order_items", ["order_id"])
    op.create_index("idx_order_items_product", "order_items", ["product_id"])


def downgrade() -> None:
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("reservations")
    op.drop_table("restaurant_tables")
    op.drop_table("restaurants")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS order_status")
    op.execute("DROP TYPE IF EXISTS reservation_status")
    op.execute("DROP TYPE IF EXISTS user_role")
