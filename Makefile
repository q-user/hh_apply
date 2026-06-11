# Makefile for HH Applicant Tool Local Development (issue #49)
#
# Usage:
#   make help          - Show this help
#   make up            - Start development stack (app + redis)
#   make up-all        - Start all services (app + redis + postgres + mailhog + minio)
#   make down          - Stop and remove containers
#   make logs          - Follow logs for all services
#   make shell         - Open shell in app container
#   make test          - Run tests in app container
#   make test-cov      - Run tests with coverage
#   make lint          - Run ruff linting
#   make lint-fix      - Auto-fix linting issues
#   make migrate       - Run database migrations
#   make db-shell      - Open PostgreSQL shell
#   make redis-shell   - Open Redis CLI
#   make build         - Build/rebuild Docker images
#   make clean         - Remove containers, volumes, and images
#   make prod-up       - Start production services
#   make prod-down     - Stop production services

.PHONY: help up up-all down logs shell test test-cov lint lint-fix migrate db-shell redis-shell build clean prod-up prod-down

# Default target
help:
	@echo "HH Applicant Tool - Local Development Commands"
	@echo ""
	@echo "Development Stack:"
	@echo "  make up            - Start core dev stack (app + redis)"
	@echo "  make up-all        - Start all services (app + redis + postgres + mailhog + minio)"
	@echo "  make down          - Stop and remove containers"
	@echo "  make logs          - Follow logs for all services"
	@echo "  make shell         - Open shell in app container"
	@echo ""
	@echo "Testing & Code Quality:"
	@echo "  make test          - Run tests in app container"
	@echo "  make test-cov      - Run tests with coverage report"
	@echo "  make lint          - Run ruff linting"
	@echo "  make lint-fix      - Auto-fix linting issues"
	@echo ""
	@echo "Database:"
	@echo "  make migrate       - Run database migrations"
	@echo "  make db-shell      - Open PostgreSQL shell (requires postgres profile)"
	@echo "  make redis-shell   - Open Redis CLI"
	@echo ""
	@echo "Build & Cleanup:"
	@echo "  make build         - Build/rebuild Docker images"
	@echo "  make clean         - Remove containers, volumes, and images"
	@echo ""
	@echo "Production:"
	@echo "  make prod-up       - Start production services (collector, tg_bot, apply_worker)"
	@echo "  make prod-down     - Stop production services"
	@echo ""

# Development stack
up:
	docker compose up -d

up-all:
	docker compose --profile postgres --profile mailhog --profile minio up -d

down:
	docker compose down

logs:
	docker compose logs -f

shell:
	docker compose exec app bash

# Testing
test:
	docker compose exec app pytest tests/ -q

test-cov:
	docker compose exec app pytest tests/ --cov=src/hh_applicant_tool --cov-report=term-missing --cov-report=html

# Linting
lint:
	docker compose exec app ruff check src/ tests/

lint-fix:
	docker compose exec app ruff check --fix src/ tests/

# Database
migrate:
	docker compose exec app hh-applicant-tool migrate-db

db-shell:
	docker compose exec postgres psql -U $${POSTGRES_USER:-hh_user} -d $${POSTGRES_DB:-hh_applicant_tool}

redis-shell:
	docker compose exec redis redis-cli

# Build
build:
	docker compose build --no-cache

# Cleanup
clean:
	docker compose down -v --rmi all --remove-orphans

# Production
prod-up:
	docker compose --profile prod up -d

prod-down:
	docker compose --profile prod down
