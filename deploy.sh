#!/usr/bin/env bash
# TableFlow deploys.
#
#   ./deploy.sh compose [--sharded] [--monitoring] [--seed]   whole stack on this machine
#   ./deploy.sh k8s <env> [--tag TAG] [--no-build] [--yes]     deploy to Kubernetes
#   ./deploy.sh diff <env> [--tag TAG]                         what a deploy would change
#   ./deploy.sh seed <env>                                     load demo data into a cluster
#   ./deploy.sh status <env>                                   workloads, revisions, health
#   ./deploy.sh rollback <env> [--to-revision N]               previous API and web release
#
# <env> is a directory in k8s/overlays: dev, dev-sharded, staging, prod.
#
# A Kubernetes deploy runs in this order and stops at the first failure:
#   build + push images -> secrets -> databases ready -> migration Job
#   -> API and web rollout -> smoke test
# If the rollout or the smoke test fails, API and web are rolled back to
# the previous revision. Migrations are not rolled back: each one is
# written to work with the release before it (docs/MIGRATION.md).
#
# Environment:
#   REGISTRY       image registry for staging/prod (default ghcr.io/wyco68)
#   KUBE_CONTEXT   kubectl context (default: the current one)
#   IMAGE_TAG      tag to deploy (default: the git commit)
#   POSTGRES_PASSWORD MONGO_PASSWORD JWT_SECRET
#                  create or update tableflow-secrets (required the first
#                  time on staging/prod; generated on dev)

set -euo pipefail

cd "$(dirname "$0")"

REGISTRY=${REGISTRY:-ghcr.io/wyco68}
RENDERED=$(mktemp)
trap 'rm -f "$RENDERED"' EXIT
ROLLOUT_TIMEOUT=${ROLLOUT_TIMEOUT:-300s}
MIGRATION_TIMEOUT=${MIGRATION_TIMEOUT:-900}

# ----------------------------------------------------------------- output

if [[ -t 1 ]]; then
  bold=$'\e[1m' red=$'\e[31m' green=$'\e[32m' yellow=$'\e[33m' reset=$'\e[0m'
else
  bold='' red='' green='' yellow='' reset=''
fi
step() { echo "${bold}==> $*${reset}"; }
ok() { echo "${green}    $*${reset}"; }
warn() { echo "${yellow}    $*${reset}" >&2; }
die() {
  echo "${red}error: $*${reset}" >&2
  exit 1
}

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

need() {
  for tool in "$@"; do
    command -v "$tool" >/dev/null || die "$tool is not installed"
  done
}

# ---------------------------------------------------------------- helpers

random() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

git_tag() {
  local tag
  tag=$(git rev-parse --short=7 HEAD)
  # Unique tag for uncommitted work, so the cluster always pulls the new
  # image instead of reusing an older one with the same name.
  if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    tag="$tag-dirty-$(date +%s)"
  fi
  echo "$tag"
}

kc() { kubectl ${KUBE_CONTEXT:+--context "$KUBE_CONTEXT"} "$@"; }
k() { kc -n "$NS" "$@"; }

overlay_dir() {
  local dir="k8s/overlays/$1"
  [[ -f $dir/kustomization.yaml ]] || die "no overlay $dir (have: $(cd k8s/overlays && echo */))"
  echo "$dir"
}

# Renders the overlay with every TableFlow image pinned to $TAG.
render() {
  local env=$1 out=$2
  kc kustomize "$(overlay_dir "$env")" >"$out"
  sed -i -E "s#^( *image: [^ ]*tableflow-(api|web|mongo-sharded)):[A-Za-z0-9._-]+#\1:$TAG#" "$out"
  NS=$(awk '/^kind: Namespace/ { ns = 1 } ns && /^  name:/ { print $2; exit }' "$out")
  [[ -n $NS ]] || die "overlay $env has no Namespace"
}

uses_sharding() { grep -q 'tableflow-mongo-sharded' "$1"; }

# Images the overlay asks for, e.g. ghcr.io/wyco68/tableflow-api:abc123.
images_in() { grep -oE 'image: [^ ]*tableflow-[a-z-]+:[A-Za-z0-9._-]+' "$1" | awk '{print $2}' | sort -u; }

build_images() {
  local rendered=$1 image name dockerfile
  for image in $(images_in "$rendered"); do
    name=${image##*/}
    name=${name%%:*}
    case $name in
      tableflow-api) dockerfile=Dockerfile ;;
      tableflow-web) dockerfile=docker/web/Dockerfile ;;
      tableflow-mongo-sharded) dockerfile=docker/mongo-sharded/Dockerfile ;;
      *) die "no Dockerfile for $name" ;;
    esac
    step "build $image"
    docker build -q -f "$dockerfile" -t "$image" \
      --build-arg GIT_SHA="$TAG" --build-arg BUILD_DATE="$(date -u +%FT%TZ)" . >/dev/null
    ship_image "$image"
  done
}

# Local clusters load the image directly; anything else pulls from a registry.
ship_image() {
  local image=$1 context
  context=${KUBE_CONTEXT:-$(kubectl config current-context)}
  case $context in
    minikube) minikube image load "$image" ;;
    kind-*) kind load docker-image "$image" --name "${context#kind-}" ;;
    docker-desktop) ;; # shares the host's images
    *) docker push -q "$image" >/dev/null ;;
  esac
  ok "$image -> $context"
}

ensure_namespace() {
  kc create namespace "$NS" --dry-run=client -o yaml | kc apply -f - >/dev/null
}

# tableflow-secrets is never in git. On dev it is generated once; on other
# environments the values come from the caller (CI secrets).
ensure_secrets() {
  local env=$1 rendered=$2 changed=0
  if [[ -n ${POSTGRES_PASSWORD:-} && -n ${MONGO_PASSWORD:-} && -n ${JWT_SECRET:-} ]]; then
    k create secret generic tableflow-secrets \
      --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
      --from-literal=MONGO_PASSWORD="$MONGO_PASSWORD" \
      --from-literal=JWT_SECRET="$JWT_SECRET" \
      --dry-run=client -o yaml | k apply -f - | grep -q configured && changed=1
    ok "tableflow-secrets from the environment"
  elif k get secret tableflow-secrets >/dev/null 2>&1; then
    ok "tableflow-secrets exists"
  elif [[ $env == dev* ]]; then
    k create secret generic tableflow-secrets \
      --from-literal=POSTGRES_PASSWORD="$(random 32)" \
      --from-literal=MONGO_PASSWORD="$(random 32)" \
      --from-literal=JWT_SECRET="$(random 64)" >/dev/null
    ok "tableflow-secrets generated"
  else
    die "tableflow-secrets is missing in $NS; set POSTGRES_PASSWORD, MONGO_PASSWORD and JWT_SECRET"
  fi

  if uses_sharding "$rendered" && ! k get secret mongo-keyfile >/dev/null 2>&1; then
    # Shared by every member of the cluster. Rotating it means restarting
    # all of them at once, so it is created once and left alone.
    k create secret generic mongo-keyfile \
      --from-literal=keyfile="$(head -c 756 /dev/urandom | base64 | tr -d '\n')" >/dev/null
    ok "mongo-keyfile generated"
  fi
  SECRETS_CHANGED=$changed
}

wait_for_databases() {
  local workload
  for workload in $(k get statefulset,deployment -l app.kubernetes.io/component=database -o name); do
    k rollout status "$workload" --timeout="$ROLLOUT_TIMEOUT" >/dev/null || die "$workload is not ready"
    ok "$workload ready"
  done
}

run_migrations() {
  local rendered=$1 state stuck applied waited=0
  # A Job's pod template cannot change, so each deploy replaces the Job.
  k delete job migrate --ignore-not-found >/dev/null
  k apply -f "$rendered" -l app.kubernetes.io/component=migration >/dev/null
  while ((waited < MIGRATION_TIMEOUT)); do
    state=$(k get job migrate -o jsonpath='{.status.conditions[?(@.status=="True")].type}')
    case $state in
      *Complete*)
        applied=$(k logs job/migrate -c migrate 2>/dev/null | grep -cE 'Running upgrade' || true)
        ok "migrations applied: ${applied:-0}"
        return 0
        ;;
      *Failed*)
        k logs job/migrate -c migrate --tail=30 >&2 || true
        die "migration failed; API and web were not changed"
        ;;
    esac
    # A pod that cannot start never counts as failed: stop on it at once
    # instead of waiting out the timeout.
    stuck=$(k get pods -l job-name=migrate -o jsonpath='{range .items[*].status.initContainerStatuses[*]}{.state.waiting.reason}{" "}{end}{range .items[*].status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}')
    if [[ $stuck =~ (ErrImagePull|ImagePullBackOff|ErrImageNeverPull|InvalidImageName|CreateContainerConfigError) ]]; then
      die "migration pod cannot start (${BASH_REMATCH[1]}); API and web were not changed"
    fi
    sleep 5
    waited=$((waited + 5))
  done
  die "migration did not finish in ${MIGRATION_TIMEOUT}s"
}

revision() {
  k get deployment "$1" -o jsonpath='{.metadata.annotations.deployment\.kubernetes\.io/revision}' 2>/dev/null || true
}

# Revisions before this deploy. Rolling back with a plain "rollout undo"
# would also move a deployment this deploy did not change to an older
# release, so only the ones that changed go back, to exactly where they were.
declare -A REVISION_BEFORE=()
remember_revisions() {
  local deploy
  for deploy in api web; do
    REVISION_BEFORE[$deploy]=$(revision "$deploy")
  done
}

rollback() {
  local deploy before
  for deploy in api web; do
    before=${REVISION_BEFORE[$deploy]:-}
    if [[ -z $before ]]; then
      warn "$deploy: first release, nothing to roll back to"
    elif [[ $(revision "$deploy") != "$before" ]]; then
      warn "$deploy: rolling back to revision $before"
      k rollout undo "deployment/$deploy" --to-revision="$before" >/dev/null
      k rollout status "deployment/$deploy" --timeout="$ROLLOUT_TIMEOUT" >/dev/null || true
    fi
  done
}

# Talks to the web Service through a port-forward, so it tests what users
# reach (nginx -> API -> databases) without depending on DNS or TLS.
smoke_test() {
  local port pf_pid url status=0
  port=$((20000 + RANDOM % 20000))
  k port-forward svc/web "$port:8080" >/dev/null 2>&1 &
  pf_pid=$!
  for _ in $(seq 1 20); do
    curl -fs -o /dev/null "http://127.0.0.1:$port/healthz" && break
    sleep 1
  done
  for url in /health/ready "/api/v1/restaurants?limit=1" "/api/v1/products?limit=1" /; do
    if curl -fs -o /dev/null --max-time 10 "http://127.0.0.1:$port$url"; then
      ok "GET $url"
    else
      warn "GET $url failed"
      status=1
    fi
  done
  kill "$pf_pid" 2>/dev/null || true
  return $status
}

confirm() {
  [[ ${ASSUME_YES:-0} == 1 || -n ${CI:-} ]] && return 0
  local reply
  read -r -p "$1 [y/N] " reply
  [[ $reply == [yY]* ]] || die "cancelled"
}

# ----------------------------------------------------------------- k8s

cmd_k8s() {
  local env=${1:-} build=1 deploy
  [[ -n $env ]] || usage 1
  shift
  while (($#)); do
    case $1 in
      --tag) TAG=$2; shift ;;
      --no-build) build=0 ;;
      --yes) ASSUME_YES=1 ;;
      *) die "unknown option $1" ;;
    esac
    shift
  done
  need kubectl docker curl
  TAG=${TAG:-${IMAGE_TAG:-$(git_tag)}}

  if [[ $env == staging || $env == prod ]] && [[ $TAG == *dirty* ]]; then
    die "$env only gets committed code; commit or stash first"
  fi

  render "$env" "$RENDERED"

  step "deploy $TAG to $env (namespace $NS, context ${KUBE_CONTEXT:-$(kubectl config current-context)})"
  [[ $env == prod ]] && confirm "Deploy $TAG to production?"

  ((build)) && build_images "$RENDERED"

  step "secrets"
  ensure_namespace
  ensure_secrets "$env" "$RENDERED"

  step "databases and shared objects"
  k apply -f "$RENDERED" -l 'app.kubernetes.io/component notin (api,web,migration)' >/dev/null
  wait_for_databases

  step "migrations"
  run_migrations "$RENDERED"

  step "rollout"
  remember_revisions
  k apply -f "$RENDERED" -l 'app.kubernetes.io/component in (api,web)' >/dev/null
  if ((SECRETS_CHANGED)); then
    k rollout restart deployment/api >/dev/null
  fi
  for deploy in api web; do
    if ! k rollout status "deployment/$deploy" --timeout="$ROLLOUT_TIMEOUT" >/dev/null; then
      warn "deployment/$deploy did not become ready"
      rollback
      die "deploy of $TAG to $env failed at rollout; previous release restored"
    fi
    ok "deployment/$deploy at $TAG"
  done

  step "smoke test"
  if ! smoke_test; then
    rollback
    die "deploy of $TAG to $env failed the smoke test; previous release restored"
  fi

  step "done: $TAG is live on $env"
  k get ingress tableflow -o jsonpath='    http://{.spec.rules[0].host}{"\n"}' 2>/dev/null || true
}

cmd_diff() {
  local env=${1:-}
  [[ -n $env ]] || usage 1
  shift
  [[ ${1:-} == --tag ]] && TAG=$2
  TAG=${TAG:-${IMAGE_TAG:-$(git_tag)}}
  render "$env" "$RENDERED"
  # kubectl diff exits 1 when there are differences.
  k diff -f "$RENDERED" || [[ $? == 1 ]]
}

cmd_seed() {
  local env=${1:-} config image
  [[ -n $env ]] || usage 1
  TAG=${IMAGE_TAG:-$(git_tag)}
  render "$env" "$RENDERED"
  [[ $env == prod ]] && confirm "Load demo data into production?"
  config=$(k get deployment api -o jsonpath='{.spec.template.spec.containers[0].envFrom[0].configMapRef.name}')
  image=$(k get deployment api -o jsonpath='{.spec.template.spec.containers[0].image}')
  step "seed $NS with $image"
  k delete job seed --ignore-not-found >/dev/null
  k apply -f - >/dev/null <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: seed
spec:
  backoffLimit: 1
  ttlSecondsAfterFinished: 3600
  template:
    metadata:
      labels:
        tableflow.io/db-client: "true"
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: seed
          image: $image
          command: ["python", "scripts/seed.py"]
          envFrom:
            - configMapRef:
                name: $config
            - secretRef:
                name: tableflow-secrets
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
EOF
  k wait --for=condition=complete job/seed --timeout=600s >/dev/null || {
    k logs job/seed --tail=30 >&2
    die "seed failed"
  }
  k logs job/seed --tail=1
}

cmd_status() {
  local env=${1:-}
  [[ -n $env ]] || usage 1
  TAG=unused
  render "$env" "$RENDERED"
  step "$env ($NS)"
  k get deployments,statefulsets,hpa -o wide
  echo
  step "API releases"
  k get replicasets -l app.kubernetes.io/name=tableflow-api --sort-by=.metadata.creationTimestamp \
    -o custom-columns='REVISION:.metadata.annotations.deployment\.kubernetes\.io/revision,IMAGE:.spec.template.spec.containers[0].image,READY:.status.readyReplicas,CREATED:.metadata.creationTimestamp'
  echo
  step "health"
  k exec deployment/api -- python -c \
    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())"
}

cmd_rollback() {
  local env=${1:-} revision='' deploy
  [[ -n $env ]] || usage 1
  shift
  [[ ${1:-} == --to-revision ]] && revision=$2
  TAG=unused
  render "$env" "$RENDERED"
  confirm "Roll back api and web in $NS${revision:+ to revision $revision}?"
  for deploy in api web; do
    k rollout undo "deployment/$deploy" ${revision:+--to-revision="$revision"}
    k rollout status "deployment/$deploy" --timeout="$ROLLOUT_TIMEOUT"
  done
}

# --------------------------------------------------------------- compose

cmd_compose() {
  local profiles=(--profile app) seed=0 port
  while (($#)); do
    case $1 in
      --sharded) profiles+=(--profile sharded); export API_MONGO_HOST=mongos ;;
      --monitoring) profiles+=(--profile monitoring) ;;
      --seed) seed=1 ;;
      *) die "unknown option $1" ;;
    esac
    shift
  done
  need docker curl
  [[ -f .env ]] || scripts/make_env.sh
  GIT_SHA=$(git_tag)
  export GIT_SHA

  step "build and start (${profiles[*]})"
  docker compose "${profiles[@]}" up -d --build --wait

  if ((seed)); then
    step "seed"
    docker compose "${profiles[@]}" run --rm api python scripts/seed.py | tail -1
  fi

  step "smoke test"
  port=$(grep -E '^WEB_PORT=' .env | cut -d= -f2)
  port=${port:-8080}
  for url in /health/ready "/api/v1/restaurants?limit=1" /; do
    curl -fs -o /dev/null --max-time 10 "http://localhost:$port$url" || die "GET $url failed"
    ok "GET $url"
  done
  step "done"
  echo "    app      http://localhost:$port"
  [[ " ${profiles[*]} " == *" monitoring "* ]] && echo "    grafana  http://localhost:3000"
  return 0
}

# ------------------------------------------------------------------ main

case ${1:-} in
  compose) shift; cmd_compose "$@" ;;
  k8s) shift; cmd_k8s "$@" ;;
  diff) shift; cmd_diff "$@" ;;
  seed) shift; cmd_seed "$@" ;;
  status) shift; cmd_status "$@" ;;
  rollback) shift; cmd_rollback "$@" ;;
  -h | --help | help) usage 0 ;;
  *) usage 1 ;;
esac
