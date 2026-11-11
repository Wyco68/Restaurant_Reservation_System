# syntax=docker/dockerfile:1.7
#
# MongoDB for the sharded topology: config server, shards and mongos all
# run this image with a different ROLE. The scripts are baked in so
# compose and Kubernetes run the same setup code.
#
#   docker build -f docker/mongo-sharded/Dockerfile -t tableflow-mongo-sharded .

# 7.0: 6.0 reached end of life in July 2025 and its image is no longer
# rebuilt. 8.0 does not start on Linux 6.19+ yet (SERVER-121912).
FROM mongo:9.0

# gosu is only used by the official entrypoint, which this image replaces;
# every member already starts as the mongodb user. An unused binary is
# attack surface the scanner keeps flagging.
RUN rm /usr/local/bin/gosu

COPY --chmod=755 docker/mongo-sharded/entrypoint.sh /usr/local/bin/tf-mongo
COPY --chmod=755 docker/mongo-sharded/status.sh /usr/local/bin/tf-shard-status
COPY docker/mongo-sharded/shard-setup.js /usr/local/share/tableflow/shard-setup.js

# The official image's mongodb user.
USER 999
ENTRYPOINT ["tf-mongo"]
