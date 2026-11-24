.PHONY: up down logs ps test build clean dev-backend dev-frontend install

## up: start the whole stack (backend :8000, frontend :3000)
up:
	docker compose up -d --build

## down: stop the stack
down:
	docker compose down

## logs: follow all container logs
logs:
	docker compose logs -f

## ps: show container status
ps:
	docker compose ps

## build: (re)build images without starting
build:
	docker compose build

## test: run the backend test suite
test:
	cd backend && python -m pytest

## clean: stop the stack and remove volumes (deletes chat history)
clean:
	docker compose down -v

## dev-backend: run the backend without Docker (uses .env if present)
dev-backend:
	cd backend && uvicorn app.main:app --reload --port 8000

## dev-frontend: run the Vite dev server without Docker
dev-frontend:
	cd frontend && npm run dev

## install: install backend + frontend dependencies locally
install:
	pip install -r backend/requirements-dev.txt
	cd frontend && npm install
