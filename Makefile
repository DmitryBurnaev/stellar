.DEFAULT_GOAL := help

UV ?= $(if $(wildcard /opt/homebrew/bin/uv),/opt/homebrew/bin/uv,uv)
E2E_ARTIFACT_DIR ?= $(CURDIR)/.local/test-results/http-e2e

.PHONY: help install deps secrets run run-in-docker lint format test test-e2e \
        build-images lint-in-docker test-in-docker test-e2e-in-docker migrate migration change-admin-password

help: ## Show commands
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*? / {printf "%-25s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the locked dependencies
	$(UV) sync --locked

deps: ## Update dependencies
	$(UV) lock --upgrade
	$(UV) sync --reinstall

secrets: ## Generate a local configuration file and initial credentials
	$(UV) run --locked python -m src.modules.cli.generate_secrets

run: ## Start Stellar with local Python
	$(UV) run --locked python -m src.main

run-in-docker: ## Build and start Stellar with Compose
	docker compose up --build app

lint: ## Run Ruff, Black, and mypy
	$(UV) run --locked ruff check .
	$(UV) run --locked black --check src
	$(UV) run --locked mypy .

format: ## Format the Python source
	$(UV) run --locked black src

test: ## Run the ordinary tests
	$(UV) run --locked pytest

test-e2e: ## Run the HTTP tests against disposable PostgreSQL
	RUN_API_E2E=1 E2E_ARTIFACT_DIR="$(E2E_ARTIFACT_DIR)" $(UV) run --locked pytest src/tests/e2e --confcutdir=src/tests/e2e -m e2e -q --junitxml="$(E2E_ARTIFACT_DIR)/junit.xml"

build-images: ## Build the service and test images
	docker compose build app test

lint-in-docker: ## Run linters using the test image
	docker compose run --rm -T --no-deps -e APP_SERVICE=lint test

test-in-docker: ## Apply migrations and run tests with disposable PostgreSQL
	docker compose run --rm -T test

test-e2e-in-docker: ## Run real HTTP tests with disposable PostgreSQL
	docker compose run --rm -T --entrypoint pytest -e RUN_API_E2E=1 -e E2E_ARTIFACT_DIR=/tmp/stellar-e2e \
		test src/tests/e2e --confcutdir=src/tests/e2e -m e2e -q -o cache_dir=/tmp/stellar-pytest

migrate: ## Upgrade PostgreSQL to the current revision
	$(UV) run --locked alembic upgrade head

migration: ## Generate a migration with make migration MESSAGE="..."
	$(UV) run --locked alembic revision --autogenerate -m "$(MESSAGE)"

change-admin-password: ## Change the initial admin's password
	$(UV) run --locked python -m src.modules.cli.management
