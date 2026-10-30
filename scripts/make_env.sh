#!/usr/bin/env bash
# Writes .env from .env.example with random passwords and a random
# JWT_SECRET, so a new clone (or a CI runner) never runs on the placeholders.
#
#   scripts/make_env.sh            # writes .env, refuses to overwrite
#   scripts/make_env.sh path/.env  # another target
#   FORCE=1 scripts/make_env.sh    # overwrite
set -euo pipefail

cd "$(dirname "$0")/.."
target=${1:-.env}

if [[ -e $target && ${FORCE:-0} != 1 ]]; then
  echo "$target already exists; set FORCE=1 to overwrite it" >&2
  exit 1
fi

# Letters and digits only: the passwords go into connection URLs unescaped.
random() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(random 32)/" \
    -e "s/^MONGO_PASSWORD=.*/MONGO_PASSWORD=$(random 32)/" \
    -e "s/^JWT_SECRET=.*/JWT_SECRET=$(random 64)/" \
    .env.example >"$target"
chmod 600 "$target"
echo "wrote $target"
