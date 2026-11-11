#!/usr/bin/env bash
# Backs up both databases of the compose stack into backups/<UTC time>/.
#
#   scripts/backup.sh                 # postgres.dump + mongo.archive.gz
#   BACKUP_KEEP=14 scripts/backup.sh  # keep the newest 14 (default 7)
#
# With the sharded profile running, set MONGO_SERVICE=mongos to dump
# through the router. Kubernetes backups are CronJobs (k8s/base/backup.yaml).
set -euo pipefail

cd "$(dirname "$0")/.."
read -r -a compose <<<"${COMPOSE:-docker compose}"
keep=${BACKUP_KEEP:-7}
mongo_service=${MONGO_SERVICE:-mongo_db}
dir="backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$dir"

# Inside the containers, so no client tools are needed on this machine.
# The credentials come from the containers' own environment.
# shellcheck disable=SC2016 # expanded inside the container
"${compose[@]}" exec -T postgres_db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  >"$dir/postgres.dump"

if [[ $mongo_service == mongos ]]; then
  user_var=MONGO_USER pass_var=MONGO_PASSWORD
else
  user_var=MONGO_INITDB_ROOT_USERNAME pass_var=MONGO_INITDB_ROOT_PASSWORD
fi
# The password goes in a config file, not on the command line.
"${compose[@]}" exec -T "$mongo_service" sh -c "
  umask 077
  printf 'password: \"%s\"\n' \"\$$pass_var\" > /tmp/mongodump.yaml
  mongodump --quiet --config=/tmp/mongodump.yaml --username=\"\$$user_var\" \
    --authenticationDatabase=admin --archive --gzip
  rm -f /tmp/mongodump.yaml" >"$dir/mongo.archive.gz"

echo "wrote $dir ($(du -sh "$dir" | cut -f1))"

# Retention: newest $keep backups stay.
mapfile -t old < <(find backups -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n +"$((keep + 1))")
for d in "${old[@]}"; do
  rm -rf "$d"
  echo "removed $d"
done
