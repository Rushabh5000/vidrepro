.PHONY: up down logs api worker web test fmt

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api worker

api:
	cd backend && uvicorn vidrepro.api.main:app --reload --port 8000

worker:
	cd backend && celery -A vidrepro.worker.celery_app worker -Q q.ingest,q.vision,q.reason,q.export -l info --concurrency 2

web:
	cd web && npm run dev

test:
	cd backend && pytest -q

fmt:
	cd backend && ruff check --fix . && ruff format .
