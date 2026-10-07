# AutoHypedrop: `make help` lists every target.
# Docker targets are the deployment path; the dev targets run from a local venv.

COMPOSE ?= docker compose
PYTHON  ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help setup build pull login dry-run run up down restart logs ps update shell \
        install lint fmt test check local-login local-dry-run local-run clean

help: ## Show this help
	@echo "Usage: make <target>"
	@awk 'BEGIN {FS = ":.*## "} /^##@/ {printf "\n%s\n", substr($$0, 5)} \
	     /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Deploy with Docker

.env:
	@sed -e "s/^AHD_UID=.*/AHD_UID=$$(id -u)/" -e "s/^AHD_GID=.*/AHD_GID=$$(id -g)/" \
		.env.example > $@
	@echo "Created .env from .env.example. Edit it to add your notification webhook."

data:
	@mkdir -p $@

setup: .env | data ## Create .env and the data/ directory (safe to re-run)

build: setup ## Build the image locally
	$(COMPOSE) build autohypedrop

pull: setup ## Pull the published image from GHCR instead of building
	$(COMPOSE) pull autohypedrop

login: setup ## One-time Google sign-in: open http://localhost:6080/vnc.html
	@if [ -n "$$($(COMPOSE) ps -q autohypedrop 2>/dev/null)" ]; then \
		echo "Stopping the scheduler so it can't use the browser profile during login."; \
		echo "Run 'make up' afterwards to start it again."; \
		$(COMPOSE) stop autohypedrop; \
	fi
	$(COMPOSE) run --rm --service-ports login

dry-run: setup ## Find claimable free drops without opening them
	$(COMPOSE) run --rm autohypedrop run --dry-run

run: setup ## Run one claim cycle now
	$(COMPOSE) run --rm autohypedrop run

up: setup ## Start the scheduler in the background
	$(COMPOSE) up -d autohypedrop

down: ## Stop the scheduler
	$(COMPOSE) down

restart: setup ## Restart the scheduler (picks up .env changes)
	$(COMPOSE) up -d --force-recreate autohypedrop

logs: ## Follow scheduler logs
	$(COMPOSE) logs -f autohypedrop

ps: ## Show container status
	$(COMPOSE) ps

update: setup ## Pull the latest image (or rebuild) and restart
	$(COMPOSE) pull autohypedrop || $(COMPOSE) build autohypedrop
	$(COMPOSE) up -d autohypedrop

shell: setup ## Open a shell in a fresh container
	$(COMPOSE) run --rm --entrypoint /bin/bash autohypedrop

##@ Develop locally (no Docker)

$(BIN)/autohypedrop: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e '.[dev]'
	$(BIN)/playwright install chromium
	@touch $@

install: $(BIN)/autohypedrop ## Create .venv with the package, dev tools and Chromium

lint: install ## Lint with ruff
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

fmt: install ## Auto-format with ruff
	$(BIN)/ruff check --fix src tests
	$(BIN)/ruff format src tests

test: install ## Run the test suite
	$(BIN)/pytest

check: lint test ## Lint and test

local-login: install ## Sign in using a browser on this machine
	$(BIN)/autohypedrop login

local-dry-run: install ## Dry-run claim cycle on this machine
	$(BIN)/autohypedrop run --dry-run

local-run: install ## Claim cycle on this machine
	$(BIN)/autohypedrop run

clean: ## Remove the venv and caches (keeps data/ and .env)
	rm -rf $(VENV) build dist .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
