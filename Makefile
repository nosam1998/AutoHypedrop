# AutoHypedrop: `make help` lists every target.
# Docker targets are the deployment path; the dev targets run from a local venv.

COMPOSE ?= docker compose
PYTHON  ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help setup build pull login dry-run run record pause resume cron update shell down \
        install lint fmt typecheck test check local-login local-dry-run local-run local-record clean

help: ## Show this help
	@echo "Usage: make <target>"
	@awk 'BEGIN {FS = ":.*## "} /^##@/ {printf "\n%s\n", substr($$0, 5)} \
	     /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Deploy with Docker

.env:
	@sed -e "s/^# PUID=.*/PUID=$$(id -u)/" -e "s/^# PGID=.*/PGID=$$(id -g)/" .env.example > $@
	@echo "Created .env from .env.example. Edit it to add your Discord webhook."

data:
	@mkdir -p $@

setup: .env | data ## Create .env (with your PUID/PGID) and data/; safe to re-run

build: setup ## Build the image locally
	$(COMPOSE) build autohypedrop

pull: setup ## Pull the published image from GHCR instead of building
	$(COMPOSE) pull autohypedrop

login: setup ## One-time Google sign-in; prints the login link (also sent to Discord)
	$(COMPOSE) up -d --force-recreate login
	@echo "Following the login screen's output. Ctrl+C stops following, not the login."
	$(COMPOSE) logs -f login

dry-run: setup ## Find claimable free drops without opening them
	$(COMPOSE) run --rm autohypedrop run --dry-run

run: setup ## Run one claim cycle now
	$(COMPOSE) run --rm autohypedrop run

record: setup ## Phase 0: record a manual walk-through at http://localhost:6080/vnc.html
	$(COMPOSE) run --rm --service-ports login record

pause: | data ## Kill switch on: every command exits without opening a browser
	@touch data/KILL
	@echo "Paused (data/KILL exists). Run 'make resume' to undo."

resume: ## Kill switch off
	@rm -f data/KILL
	@echo "Resumed."

cron: ## Print a crontab line that runs a claim cycle every day
	@echo "17 9 * * * cd $(CURDIR) && $(COMPOSE) run --rm autohypedrop run >> data/cron.log 2>&1"

update: setup ## Pull the latest image, or rebuild if there is none to pull
	$(COMPOSE) pull autohypedrop || $(COMPOSE) build autohypedrop

shell: setup ## Open a root shell in a fresh container
	$(COMPOSE) run --rm --entrypoint /bin/bash autohypedrop

down: ## Stop and remove any AutoHypedrop containers
	$(COMPOSE) --profile login down

##@ Develop locally (no Docker)

$(BIN)/autohypedrop: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e '.[dev]'
	$(BIN)/python -m playwright install chromium
	@touch $@

install: $(BIN)/autohypedrop ## Create .venv with the package, dev tools and Chromium

lint: install ## Lint and check formatting with ruff
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

fmt: install ## Auto-fix lint and formatting
	$(BIN)/ruff check --fix src tests
	$(BIN)/ruff format src tests

typecheck: install ## Type-check with mypy
	$(BIN)/mypy

test: install ## Run the test suite (drives real Chromium)
	$(BIN)/pytest

check: lint typecheck test ## Everything CI runs

local-login: install ## Sign in using a browser window on this machine
	$(BIN)/autohypedrop login

local-dry-run: install ## Dry-run claim cycle on this machine
	$(BIN)/autohypedrop run --dry-run

local-run: install ## Claim cycle on this machine
	$(BIN)/autohypedrop run

local-record: install ## Phase 0 recording on this machine
	$(BIN)/autohypedrop record

clean: ## Remove the venv and caches (keeps data/ and .env)
	rm -rf $(VENV) build dist .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
