export PATH := $(HOME)/.local/bin:$(PATH)
COMPOSE = docker compose -f docker/compose.yaml $(if $(wildcard .env),--env-file .env)
COMPOSE_DEV = docker compose -f docker/compose.yaml -f docker/compose.dev.yaml $(if $(wildcard .env),--env-file .env)

.PHONY: dev test lint e2e e2e-agent api-types api-types-check i18n-check test-db

dev:
	$(COMPOSE_DEV) up --build -d --wait
	@port=$$(grep -E "^VDS_PORT=" .env 2>/dev/null | tail -1 | cut -d= -f2); echo "VDS up: http://localhost:$${port:-$${VDS_PORT:-8000}}/health"

# PostgreSQL 17 jetable pour les tests (mot de passe aléatoire, jamais écrit dans le dépôt).
# Usage : eval "$$(make -s test-db)"
test-db:
	@docker rm -f vds-test-db >/dev/null 2>&1; \
	pw=$$(head -c 18 /dev/urandom | base64 | tr -dc A-Za-z0-9); \
	docker run -d --name vds-test-db -e POSTGRES_PASSWORD=$$pw -e POSTGRES_USER=vds -e POSTGRES_DB=vds_test -p 127.0.0.1:55432:5432 postgres:17 >/dev/null; \
	echo "export VDS_TEST_DATABASE_URL=postgresql+asyncpg://vds:$$pw@127.0.0.1:55432/vds_test"

test:
	@if [ -z "$$VDS_TEST_DATABASE_URL" ]; then \
	  eval "$$($(MAKE) -s test-db)"; \
	  until docker exec vds-test-db pg_isready -U vds -d vds_test >/dev/null 2>&1; do sleep 1; done; sleep 2; \
	  uv run pytest; rc=$$?; docker rm -f vds-test-db >/dev/null; exit $$rc; \
	else uv run pytest; fi
	pnpm -r --if-present test
	$(MAKE) i18n-check

lint:
	uv run ruff check
	uv run mypy
	pnpm -r --if-present lint

e2e:
	pnpm --filter @vds/admin e2e
	pnpm --filter @vds/player e2e
	pnpm --filter @vds/player e2e:real
	$(MAKE) e2e-agent

# Vrai serveur + vrai agent (pilotes fakes TCP/WSS/UDP, faux SMTP), aux deux VDS_BASE_PATH.
# Conteneur PG jetable créé et supprimé par le test (port 4390).
e2e-agent:
	uv run pytest apps/site-agent/e2e -q -p no:cacheprovider

i18n-check:
	pnpm --filter @vds/admin i18n:check

# OpenAPI du serveur (sans DB) -> packages/sdk-ts/src/api.generated.ts (openapi-typescript épinglé)
SDK_TS = packages/sdk-ts
OPENAPI_TMP = $(SDK_TS)/.openapi.json

api-types:
	uv run python scripts/export_openapi.py $(OPENAPI_TMP)
	OPENAPI_JSON=$(CURDIR)/$(OPENAPI_TMP) pnpm --filter @vds/sdk-ts generate
	@rm -f $(OPENAPI_TMP)

# CI : échoue si le fichier généré diffère de celui versionné
api-types-check:
	@cp $(SDK_TS)/src/api.generated.ts $(SDK_TS)/.api.generated.ref
	@$(MAKE) -s api-types >/dev/null; rc=0; \
	diff -u $(SDK_TS)/.api.generated.ref $(SDK_TS)/src/api.generated.ts || rc=1; \
	rm -f $(SDK_TS)/.api.generated.ref; \
	[ $$rc -eq 0 ] && echo "api-types: à jour" || { echo "api-types: périmé, lancer make api-types"; exit 1; }
