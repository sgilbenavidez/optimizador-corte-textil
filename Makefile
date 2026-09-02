.PHONY: dev down test migrate seed

dev:
	docker compose up --build

down:
	docker compose down

test:
	docker compose run --rm api pytest
	docker compose run --rm web pnpm test:run

migrate:
	docker compose run --rm api alembic upgrade head

seed:
	docker compose run --rm api python -m costura_optima.infrastructure.seed
