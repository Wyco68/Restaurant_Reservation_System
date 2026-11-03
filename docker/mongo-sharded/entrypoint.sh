#!/bin/bash
# Starts one member of the sharded cluster and runs its one-time setup.
#
#   ROLE=configsvr | shardsvr   RS_NAME=<replica set>  RS_HOST=<name others reach it by>
#   ROLE=mongos                 CONFIG_DB=cfg/host:27017
#                               SHARDS="shard_a/host:27017 shard_b/host:27017"
#                               MONGO_USER  MONGO_PASSWORD  MONGO_DB
#
# Members authenticate to each other with a shared keyfile, so access
# control is on from the first start. With access control on and no users,
# rs.initiate and the first createUser are allowed only from localhost.
# That is why each member sets itself up from inside its own container
# instead of from a separate init container.
set -euo pipefail

: "${ROLE:?ROLE must be configsvr, shardsvr or mongos}"

# mongod refuses a keyfile that group or others can read, and a Kubernetes
# Secret volume cannot be owned by the mongodb user, so use a private copy.
keyfile=/tmp/mongo-keyfile
install -m 400 "${MONGO_KEYFILE_SOURCE:-/etc/mongo-keyfile/keyfile}" "$keyfile"
rm -f /tmp/ready

pid=
stop() {
  [[ -n $pid ]] && kill -TERM "$pid" 2>/dev/null
  wait "$pid"
  exit 0
}
trap stop TERM INT

wait_for_local() {
  until mongosh --quiet --port 27017 --eval 'db.adminCommand({ping: 1}).ok' >/dev/null 2>&1; do
    sleep 1
  done
}

case "$ROLE" in
  configsvr | shardsvr)
    : "${RS_NAME:?RS_NAME is required}"
    host=${RS_HOST:-$(hostname)}
    mongod "--$ROLE" --replSet "$RS_NAME" --port 27017 --bind_ip_all --keyFile "$keyfile" &
    pid=$!
    wait_for_local
    configsvr=false
    [[ $ROLE == configsvr ]] && configsvr=true
    # db.hello() needs no login. setName is missing until the set exists.
    mongosh --quiet --port 27017 --eval "
      if (!db.hello().setName) {
        rs.initiate({_id: '$RS_NAME', configsvr: $configsvr, members: [{_id: 0, host: '$host:27017'}]});
        print('initiated replica set $RS_NAME as $host:27017');
      }"
    ;;

  mongos)
    : "${CONFIG_DB:?CONFIG_DB is required}"
    : "${MONGO_USER:?}" "${MONGO_PASSWORD:?}"
    mongos --configdb "$CONFIG_DB" --bind_ip_all --port 27017 --keyFile "$keyfile" &
    pid=$!
    wait_for_local
    # The credentials stay in the environment (process.env), out of argv.
    mongosh --quiet --port 27017 --eval "
      try {
        db.getSiblingDB('admin').createUser({
          user: process.env.MONGO_USER, pwd: process.env.MONGO_PASSWORD, roles: ['root'],
        });
        print('created user ' + process.env.MONGO_USER);
      } catch (e) {
        // Unauthorized: the localhost exception is closed, the user exists.
        if (e.codeName !== 'Unauthorized') throw e;
      }"
    mongosh --quiet --port 27017 --file /usr/local/share/tableflow/shard-setup.js
    ;;

  *)
    echo "unknown ROLE: $ROLE" >&2
    exit 64
    ;;
esac

touch /tmp/ready
wait "$pid"
