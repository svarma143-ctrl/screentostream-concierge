UV ?= uv
PORT ?= 8000

.PHONY: install env run dev test lint format-check format check smoke smoke-full bedrock-check docker-build docker-up docker-down reset clean

install:
	$(UV) sync --all-groups

env:
	@test -f .env || cp .env.example .env
	@echo "Wrote .env from .env.example (edit PUBLIC_BASE_URL for a tunnel)."

run:
	$(UV) run uvicorn app.main:app --host 0.0.0.0 --port $(PORT)

dev:
	$(UV) run uvicorn app.main:app --host 127.0.0.1 --port $(PORT) --reload

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check app tests scripts

format-check:
	$(UV) run ruff format --check app tests scripts

format:
	$(UV) run ruff format app tests scripts
	$(UV) run ruff check app tests scripts --fix

check: lint format-check test

smoke:
	$(UV) run python scripts/smoke.py --base-url http://127.0.0.1:$(PORT)

smoke-full:
	$(UV) run python scripts/smoke.py --base-url http://127.0.0.1:$(PORT) --full

bedrock-check:
	$(UV) run python scripts/bedrock_check.py

docker-build:
	docker build -t screentostream-concierge .

docker-up:
	docker compose up --build

docker-down:
	docker compose down

reset:
	rm -rf runtime
	@echo "Removed runtime/. The next start recreates the demo database."

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .tmp
