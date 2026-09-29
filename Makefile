.PHONY: install dev test seed demo-reset eval db-up
install:
	python -m pip install -r requirements.txt
	cd apps/web && npm ci
dev:
	cd apps/web && npm run build
	python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
test:
	python -m pytest apps/api/app/tests -q
	cd apps/web && npm run build
seed:
	python -m scripts.seed
demo-reset:
	python -m scripts.reset_demo
eval:
	python -m scripts.run_eval
db-up:
	docker compose -f infra/docker-compose.yml up -d postgres
