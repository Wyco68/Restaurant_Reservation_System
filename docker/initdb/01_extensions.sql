-- Initial DDL setup script.
-- Runs once, automatically, the first time the postgres_data volume is created.
-- Alembic owns the tables; this file owns only what Alembic cannot create
-- for itself, because extensions require privileges the migration may not have.

-- Case-insensitive text, used for users.email so that Bob@x.com and bob@x.com
-- cannot both register.
CREATE EXTENSION IF NOT EXISTS citext;

-- Required by the reservations EXCLUDE constraint. btree_gist lets a GiST index
-- mix an equality operator (restaurant_table_id WITH =) with a range overlap
-- operator (tstzrange WITH &&) in one constraint. Without it, the
-- no_double_booking constraint cannot be created.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- gen_random_uuid() for users.id.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
