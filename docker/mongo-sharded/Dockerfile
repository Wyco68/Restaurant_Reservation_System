# syntax=docker/dockerfile:1.7
#
# MongoDB for the sharded topology: config server, shards and mongos all
# run this image with a different ROLE. The scripts are baked in so
# compose and Kubernetes run the same setup code.
#
#   docker build -f docker/mongo-sharded/Dockerfile -t tableflow-mongo-sharded .

FROM mongo:6.0

COPY --chmod=755 docker/mongo-sharded/entrypoint.sh /usr/local/bin/tf-mongo
COPY --chmod=755 docker/mongo-sharded/status.sh /usr/local/bin/tf-shard-status
COPY docker/mongo-sharded/shard-setup.js /usr/local/share/tableflow/shard-setup.js

# The official image's mongodb user.
USER 999
ENTRYPOINT ["tf-mongo"]
