# Shortcuts for the common commands. Each target is one line you can also
# run by hand; deploy.sh holds the real logic.
#
#   make help

ENV ?= dev

.DEFAULT_GOAL := help
.PHONY: help env db stack stack-sharded down test backup restore images k8s diff seed status rollback

help: ## list targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

env: ## create .env with random secrets
	scripts/make_env.sh

db: ## databases and Redis only (run the API with uvicorn)
	docker compose up -d --wait

stack: ## whole stack in compose, seeded, monitoring on :3000
	./deploy.sh compose --monitoring --seed

stack-sharded: ## whole stack on sharded MongoDB
	./deploy.sh compose --sharded --seed

down: ## stop compose (data volumes are kept)
	docker compose --profile app --profile sharded --profile monitoring down

test: ## test suite (needs `make db` and a seeded database)
	pytest

backup: ## dump both compose databases into backups/
	scripts/backup.sh

restore: ## restore a backup: make restore DIR=backups/<time>
	scripts/restore.sh $(DIR)

images: ## build the API, web and MongoDB images
	docker build -t tableflow-api:local .
	docker build -f docker/web/Dockerfile -t tableflow-web:local .
	docker build -f docker/mongo-sharded/Dockerfile -t tableflow-mongo-sharded:local .

k8s: ## deploy to Kubernetes: make k8s ENV=staging
	./deploy.sh k8s $(ENV)

diff: ## what a deploy would change: make diff ENV=prod
	./deploy.sh diff $(ENV)

seed: ## load demo data into a cluster
	./deploy.sh seed $(ENV)

status: ## workloads, releases and health
	./deploy.sh status $(ENV)

rollback: ## previous API and web release
	./deploy.sh rollback $(ENV)
