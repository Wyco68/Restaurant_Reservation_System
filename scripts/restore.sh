#!/usr/bin/env bash
# Restores a backup made by scripts/backup.sh into the compose stack.
# Replaces the current data in both databases.
#
#   scripts/restore.sh backups/20261108T020000Z
#   scripts/restore.sh backups/20261108T020000Z --yes   # no prompt
set -euo pipefail

cd "$(dirname "$0")/.."
read -r -a compose <<<"${COMPOSE:-docker compose}"
mongo_service=${MONGO_SERVICE:-mongo_db}
dir=${1:?usage: scripts/restore.sh backups/<time> [--yes]}
[[ -f $dir/postgres.dump && -f $dir/mongo.archive.gz ]] || {
  echo "$dir has no postgres.dump and mongo.archive.gz" >&2
  exit 1
}

if [[ ${2:-} != --yes ]]; then
  read -r -p "Replace all PostgreSQL and MongoDB data with $dir? [y/N] " reply
  [[ $reply == [yY]* ]] || exit 1
fi

# --clean --if-exists drops each object before recreating it, so the
# restore works on a populated database.
# shellcheck disable=SC2016 # expanded inside the container
"${compose[@]}" exec -T postgres_db sh -c \
  'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <"$dir/postgres.dump"
echo "postgres restored"

if [[ $mongo_service == mongos ]]; then
  user_var=MONGO_USER pass_var=MONGO_PASSWORD db_var=MONGO_DB
else
  user_var=MONGO_INITDB_ROOT_USERNAME pass_var=MONGO_INITDB_ROOT_PASSWORD db_var=MONGO_INITDB_DATABASE
fi

# Empty the collections instead of mongorestore --drop: dropping a sharded
# collection also drops its shard key, and it would come back unsharded.
# Emptying keeps indexes and sharding as they are.
"${compose[@]}" exec -T "$mongo_service" sh -c "
  HOME=/tmp mongosh --quiet --eval '
    db.getSiblingDB(\"admin\").auth(process.env.$user_var, process.env.$pass_var);
    const target = db.getSiblingDB(process.env.$db_var);
    const names = target.getCollectionNames();
    names.forEach((name) => target.getCollection(name).deleteMany({}));
    print(\"emptied \" + names.length + \" collections\");
  '"

"${compose[@]}" exec -T "$mongo_service" sh -c "
  umask 077
  printf 'password: \"%s\"\n' \"\$$pass_var\" > /tmp/mongorestore.yaml
  mongorestore --quiet --config=/tmp/mongorestore.yaml --username=\"\$$user_var\" \
    --authenticationDatabase=admin --archive --gzip --nsExclude='admin.*' --nsExclude='config.*'
  rm -f /tmp/mongorestore.yaml" <"$dir/mongo.archive.gz"
echo "mongo restored"
