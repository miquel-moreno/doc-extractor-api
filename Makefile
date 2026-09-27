# Common tasks. Run `make help` to list them.
PKG := doc_extractor_api

.PHONY: help install dev lint format typecheck test check eval up down

help:
	@echo "install    Install dependencies and git hooks"
	@echo "dev        Run the API with auto-reload on http://localhost:8000"
	@echo "lint       Check style and common bugs (ruff)"
	@echo "format     Auto-format the code (ruff)"
	@echo "typecheck  Check types (mypy strict)"
	@echo "test       Run tests with coverage (min 80%)"
	@echo "check      lint + typecheck + test (run before every commit)"
	@echo "eval       Run the evaluation against a real LLM (local only, costs money)"
	@echo "up / down  Start / stop everything with Docker Compose"

install:
	uv sync
	uv run pre-commit install

dev:
	uv run uvicorn $(PKG).main:app --reload

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy src

test:
	uv run pytest --cov --cov-report=term-missing --cov-fail-under=80

check: lint typecheck test

eval:
	uv run python -m evals.run

up:
	docker compose up --build

down:
	docker compose down
