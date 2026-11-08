# Deployment and Operations

Runbook for running TableFlow outside a developer's terminal: the whole stack in Docker, Kubernetes, CI/CD, rollback, backups and monitoring. Why it is built this way: [DESIGN.md](DESIGN.md#platform).

| Environment | Runs on | How | MongoDB |
|---|---|---|---|
| compose | your machine | `./deploy.sh compose` | single, or sharded with `--sharded` |
| `dev` | minikube or kind | `./deploy.sh k8s dev` | single |
| `dev-sharded` | minikube or kind | `./deploy.sh k8s dev-sharded` | 2 shards x 3 members |
| `staging` | shared cluster | CI, on every push to `main` | single |
| `prod` | production cluster | CI, manual run with approval | 2 shards x 3 members |

Requires Docker. Kubernetes targets also need `kubectl` and a cluster context; `make help` lists the shortcuts.

---

## Whole stack in Docker

```bash
./deploy.sh compose --seed                 # creates .env if missing, builds, starts, seeds, smoke-tests
./deploy.sh compose --monitoring           # + Prometheus and Grafana
./deploy.sh compose --sharded --seed       # MongoDB as config server + 2 shards + mongos
```

| URL | What |
|---|---|
| http://localhost:8080 | the app (nginx serves the client and proxies `/api`) |
| http://localhost:8000/docs | API, direct |
| http://localhost:3000 | Grafana, *TableFlow API* dashboard (`--monitoring`) |
| http://localhost:9090/alerts | Prometheus alert rules (`--monitoring`) |

Without `deploy.sh`, the same with compose profiles:

```bash
scripts/make_env.sh                                   # .env with random secrets
docker compose --profile app up -d --build --wait
docker compose --profile app run --rm api python scripts/seed.py
```

`docker compose up -d` with no profile still starts only the databases and Redis, for running the API with uvicorn ([README](../README.md#quick-start)).

Stop: `make down`. Data volumes are kept; `docker compose down -v` deletes them.

---

## Kubernetes

### Deploy

```bash
./deploy.sh k8s dev            # local cluster: builds the images and loads them into it
./deploy.sh diff staging       # what would change, without changing it
./deploy.sh k8s staging        # builds, pushes to $REGISTRY, deploys
./deploy.sh status staging     # workloads, API releases, /health
```

Every TableFlow image is tagged with the commit (`git rev-parse --short=7 HEAD`). Staging and prod refuse uncommitted work.

What `deploy.sh k8s` does, stopping at the first failure:

1. Build the API, web and (if the overlay is sharded) MongoDB images; load them into minikube or kind, or push them.
2. Create `tableflow-secrets` and, for sharded overlays, `mongo-keyfile` if missing (see [Secrets](#secrets)).
3. Apply everything except the API, web and migration Job; wait for the databases.
4. Run the `migrate` Job (`alembic upgrade head`) and wait for it. If its pod cannot start or the migration fails, stop: the running release is untouched.
5. Apply API and web; wait for the rollout. Old pods keep serving until new ones pass `/health/ready` (`maxUnavailable: 0`).
6. Smoke-test through the web Service: `/health/ready`, a restaurant search, the catalogue, `/`.
7. If step 5 or 6 fails, roll API and web back to the revisions they had before step 5.

A first deploy to minikube takes about 40 s. A release that crashes on start is rolled back in about a minute and the site keeps answering throughout.

Open the dev environment in a browser: add `$(minikube ip) tableflow.local` to `/etc/hosts`, or

```bash
kubectl -n tableflow-dev port-forward svc/web 8080:8080
```

### Load demo data

```bash
./deploy.sh seed dev
```

Runs `scripts/seed.py` as a Job with the deployed API image. On prod it asks first.

### Secrets

Nothing secret is in git. `deploy.sh` creates `tableflow-secrets` (`POSTGRES_PASSWORD`, `MONGO_PASSWORD`, `JWT_SECRET`):

| Environment | Source |
|---|---|
| `dev`, `dev-sharded` | generated on first deploy |
| `staging`, `prod` | the variables of the same names, which CI takes from GitHub environment secrets; required on the first deploy |

Passing new values updates the Secret and restarts the API. The database password itself is set only when the database volume is first created, so rotating `POSTGRES_PASSWORD` means `ALTER USER` first, then deploy with the new value.

`mongo-keyfile` is generated once and never rotated by `deploy.sh`: every cluster member must switch at the same moment.

### Rollback

```bash
./deploy.sh rollback prod                    # API and web to the previous revision
./deploy.sh rollback prod --to-revision 12   # to a revision listed by status
```

From GitHub: **Actions > Deploy > Run workflow**, action `rollback`.

Migrations are not rolled back. Each one works with the code before it ([MIGRATION.md](MIGRATION.md)), so the previous release runs on the new schema. To undo a migration as well: `kubectl -n <ns> exec deploy/api -- alembic downgrade -1`, after the rollback.

---

## CI/CD

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), on every push and pull request:

| Job | Checks |
|---|---|
| `lint` | ruff, shellcheck, hadolint, `docker compose config`, `promtool check rules` |
| `test` | migrations up, down to base and up again; seed; full pytest with Redis |
| `manifests` | every overlay through kubeconform |
| `e2e` | `deploy.sh k8s dev` and `deploy.sh seed dev` on a kind cluster |
| `images` | build, Trivy scan (fails on fixable critical CVEs); on push, publish to GHCR with SBOM and provenance |

Images are `ghcr.io/wyco68/tableflow-{api,web,mongo-sharded}:<short commit>`, plus `main` and the version on `v*` tags.

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) runs `deploy.sh` with `--no-build`, on the images CI pushed:

- after CI passes on `main`: deploy to staging
- **Run workflow**: staging or prod, `deploy` or `rollback`, optional image tag

One-time GitHub setup, under **Settings > Environments**, for `staging` and `prod`:

| Secret | Value |
|---|---|
| `KUBECONFIG` | `base64 -w0 < kubeconfig` for that cluster, scoped to its namespace |
| `POSTGRES_PASSWORD`, `MONGO_PASSWORD` | `openssl rand -hex 24` |
| `JWT_SECRET` | `openssl rand -base64 48` |

Give `prod` required reviewers so a production run waits for approval.

Third-party actions are pinned to commit SHAs; Dependabot ([`.github/dependabot.yml`](../.github/dependabot.yml)) proposes updates for them, the base images and the Python packages each week.

---

## Backups

Kubernetes: two CronJobs in every environment ([`k8s/base/backup.yaml`](../k8s/base/backup.yaml)).

| CronJob | When (Asia/Bangkok) | Writes | Keeps |
|---|---|---|---|
| `postgres-backup` | 02:00 | `pg_dump -Fc` to PVC `postgres-backups` | newest 7 |
| `mongo-backup` | 02:30 | `mongodump --gzip` (through mongos when sharded) to PVC `mongo-backups` | newest 7 |

The volumes are in the same cluster as the data. For production, copy the files to object storage as well.

Back up now:

```bash
kubectl -n tableflow create job --from=cronjob/postgres-backup postgres-backup-manual
kubectl -n tableflow create job --from=cronjob/mongo-backup mongo-backup-manual
```

Restore PostgreSQL:

```bash
NS=tableflow
kubectl -n $NS run backup-reader --image=postgres:15-alpine --restart=Never \
  --overrides='{"spec":{"securityContext":{"runAsUser":70,"fsGroup":70},"containers":[{"name":"backup-reader","image":"postgres:15-alpine","command":["sleep","600"],"volumeMounts":[{"name":"b","mountPath":"/backups"}]}],"volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"postgres-backups"}}]}}'
kubectl -n $NS exec backup-reader -- ls -1t /backups              # pick a file
kubectl -n $NS exec backup-reader -- cat /backups/<file>.dump > restore.dump
kubectl -n $NS exec -i postgres-0 -- sh -c \
  'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < restore.dump
kubectl -n $NS delete pod backup-reader
```

Compose:

```bash
make backup                                  # backups/<UTC time>/{postgres.dump,mongo.archive.gz}, newest 7 kept
make restore DIR=backups/20261108T020000Z    # asks first; replaces both databases
```

With the sharded profile, prefix both with `MONGO_SERVICE=mongos`. The restore empties collections instead of dropping them: dropping a sharded collection through mongos also removes its shard key, and it would come back unsharded.

---

## Sharded MongoDB

```bash
docker compose exec mongos tf-shard-status                       # compose
kubectl -n tableflow exec deploy/mongos -- tf-shard-status       # Kubernetes
```

Shows each sharded collection's documents per shard. On the seed data, `user_telemetry` splits about 51/49 and `reviews` 65/35 (40 restaurants hashed over 2 shards).

The API connects to `mongos` and needs no code change; `MONGO_HOST` is set by the `--sharded` flag or the `mongo-sharded` component. Shard keys and why: [DESIGN.md](DESIGN.md#sharding).

Members start in parallel; member 0 of each replica set waits for the others and runs `rs.initiate`. Each mongos creates the admin user on the first start, registers the shards and shards the collections; every step is skipped when already done, so restarts are safe.

Check failover (on `dev-sharded`): delete a primary and watch a secondary take over, usually within a few seconds.

```bash
kubectl -n tableflow-dev-sharded delete pod mongo-shard-a-0
kubectl -n tableflow-dev-sharded exec mongo-shard-a-1 -- sh -c "HOME=/tmp mongosh --quiet --eval 'db.hello().primary'"
```

---

## Monitoring

Every API pod exposes `/metrics` (scraped through the `prometheus.io/*` pod annotations; not reachable from outside). Every response carries `X-Request-ID`, and with `LOG_FORMAT=json` (the image default) every log line has the same `request_id`:

```bash
kubectl -n tableflow logs deploy/api | grep <request id>
```

Alert rules ([`docker/monitoring/alerts.yml`](../docker/monitoring/alerts.yml)) use the CP3 targets from [PERFORMANCE.md](PERFORMANCE.md):

| Alert | Fires when |
|---|---|
| `TableflowApiDown` | an API target misses scrapes for 1 min |
| `TableflowHighErrorRate` | more than 1% of requests are 5xx for 5 min |
| `TableflowSlowRequests` | p95 latency above 200 ms for 10 min |
| `TableflowCacheBypassed` | Redis unreachable for 5 min (info) |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `migration pod cannot start (ErrImagePull)` | the tag was never pushed | wait for CI's `images` job, or deploy a tag that exists |
| `migration failed` | the migration raised; logs are printed | fix and redeploy; nothing was rolled out |
| `deployment/api did not become ready`, then rollback | new pods fail `/health/ready` or crash | `kubectl -n <ns> logs deploy/api --previous`; `/health/ready` is 503 only when PostgreSQL is unreachable |
| `429 Too Many Requests` through the web tier | per-client rate limit: 30 req/s (burst 60), login 10/min (burst 5) | expected for load tests through nginx; point Locust at the API directly or raise `API_RATE_LIMIT` on the web container |
| `X-Cache: BYPASS` on every catalogue read | Redis unreachable; the API serves from the databases | `kubectl -n <ns> get pods -l app.kubernetes.io/name=redis` |
| mongos not ready | a replica set has no primary yet, or `mongo-keyfile` is missing | `kubectl -n <ns> logs deploy/mongos`; setup steps retry for two minutes |
| Upsert into `reviews` fails with *could not extract exact shard key* | sharded collection, filter without `restaurant_id` | include the shard key in the filter, as `scripts/seed.py` does |
| `would violate PodSecurity "restricted"` warnings | database images start as root | expected; namespaces enforce `baseline` and only warn on `restricted` |
