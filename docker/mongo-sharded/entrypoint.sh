#!/bin/bash
# Starts one member of the sharded cluster and runs its one-time setup.
#
#   ROLE=configsvr | shardsvr   RS_NAME=<replica set>  RS_HOST=<name others reach it by>
#                               RS_MEMBERS="<host:port> ..." (default: just this member)
#   ROLE=mongos                 CONFIG_DB=cfg/host:27017
#                               SHARDS="shard_a/host:27017 shard_b/host:27017"
#                               MONGO_USER  MONGO_PASSWORD  MONGO_DB
#   MONGOD_EXTRA_ARGS           extra mongod flags, e.g. --wiredTigerCacheSizeGB 0.25
#
# Members authenticate to each other with a shared keyfile, so access
# control is on from the first start. With access control on and no users,
# rs.initiate and the first createUser are allowed only from localhost.
# That is why each member sets itself up from inside its own container
# instead of from a separate init container.
set -euo pipefail

: "${ROLE:?ROLE must be configsvr, shardsvr or mongos}"

# mongosh keeps history under $HOME, which may be read-only.
export HOME=/tmp

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

ping() {
  mongosh --quiet --host "$1" --eval 'db.adminCommand({ping: 1}).ok' >/dev/null 2>&1
}

case "$ROLE" in
  configsvr | shardsvr)
    : "${RS_NAME:?RS_NAME is required}"
    host=${RS_HOST:-$(hostname)}:27017
    members=${RS_MEMBERS:-$host}
    # shellcheck disable=SC2086 # MONGOD_EXTRA_ARGS is a list of flags
    mongod "--$ROLE" --replSet "$RS_NAME" --port 27017 --bind_ip_all \
      --keyFile "$keyfile" ${MONGOD_EXTRA_ARGS:-} &
    pid=$!
    until ping localhost:27017; do sleep 1; done

    # The first listed member creates the set once every member answers.
    # The others receive the configuration from it.
    if [[ ${members%% *} == "$host" ]]; then
      for member in $members; do
        until ping "$member"; do
          echo "waiting for $member"
          sleep 2
        done
      done
      configsvr=false
      [[ $ROLE == configsvr ]] && configsvr=true
      # db.hello() needs no login. setName is missing until the set exists.
      RS_MEMBERS=$members CONFIGSVR=$configsvr mongosh --quiet --port 27017 --eval "
        if (!db.hello().setName) {
          const members = process.env.RS_MEMBERS.split(' ').map((host, _id) => ({_id, host}));
          rs.initiate({_id: '$RS_NAME', configsvr: process.env.CONFIGSVR === 'true', members});
          print('initiated replica set $RS_NAME: ' + process.env.RS_MEMBERS);
        }"
    fi
    ;;

  mongos)
    : "${CONFIG_DB:?CONFIG_DB is required}"
    : "${MONGO_USER:?}" "${MONGO_PASSWORD:?}"
    mongos --configdb "$CONFIG_DB" --bind_ip_all --port 27017 --keyFile "$keyfile" &
    pid=$!
    until ping localhost:27017; do sleep 1; done
    # Credentials are read from process.env inside the script, not argv.
    mongosh --quiet --port 27017 --file /usr/local/share/tableflow/shard-setup.js
    ;;

  *)
    echo "unknown ROLE: $ROLE" >&2
    exit 64
    ;;
esac

touch /tmp/ready
wait "$pid"
