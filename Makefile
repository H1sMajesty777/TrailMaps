.PHONY: install lint test up up-dev down migrate test-services

install:
	cd backend && pip install -r requirements-dev.txt

lint:
	cd backend && ruff check .

test-services:
	docker compose -f docker-compose.test.yml up -d

test: test-services
	cd backend && pytest --cov=app --cov-report=term-missing

up:
	docker compose up -d --build

up-dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

down:
	docker compose down

migrate:
	docker compose run --rm migrate
