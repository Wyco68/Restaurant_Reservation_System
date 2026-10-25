-- Database setup script.
-- Runs once, the first time the postgres_data volume is created.
-- Alembic creates the tables. This file only creates the extensions, which
-- need more privileges than the migration user may have.

-- Case-insensitive text for users.email, so Bob@x.com and bob@x.com are the
-- same account.
CREATE EXTENSION IF NOT EXISTS citext;

-- Needed for the no_double_booking constraint, which uses = on
-- restaurant_table_id and && on the time range in one GiST index.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- gen_random_uuid() for users.id.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
