.PHONY: install test lint typecheck web-install web-build demo up

install:
	uv sync
	cd apps/web && npm install

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy packages
	cd apps/web && npm run typecheck

web-install:
	cd apps/web && npm install

web-build:
	cd apps/web && npm run build

demo:
	uv run uvicorn app:app --app-dir examples/demo-agent --port 8000

up:
	docker compose up --build
