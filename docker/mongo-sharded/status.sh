#!/bin/bash
# Prints how each sharded collection is spread over the shards.
#   docker compose exec mongos tf-shard-status
set -euo pipefail
export HOME=/tmp
mongosh --quiet --port 27017 --eval "
  db.getSiblingDB('admin').auth(process.env.MONGO_USER, process.env.MONGO_PASSWORD);
  const target = db.getSiblingDB(process.env.MONGO_DB || 'tableflow');
  for (const name of ['user_telemetry', 'reviews']) {
    print('== ' + name);
    print(target.getCollection(name).getShardDistribution());
  }"
