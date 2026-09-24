# Деплой (Docker)

Production-стек web-приложения: Docker-образ + PostgreSQL + (опционально) Stripe billing.

## Образ

`Dockerfile` — multi-stage на `python:3.12-slim`:

- только runtime-пакеты (`requirements-server.txt`), итоговый образ ~474 MB
- non-root пользователь `fluxion`
- данные — в `/app/data`
- `HEALTHCHECK` — `curl http://localhost:8000/api/health`
- порт `8000`

```bash
docker build -t fluxion .
docker run -p 8000:8000 -e FLUXION_JWT_SECRET=$(openssl rand -hex 32) fluxion
```

## Production compose

`docker-compose.prod.yml` — приложение + PostgreSQL 16 (healthcheck `pg_isready`, том `fluxion_pgdata_prod`).

Обязательная переменная:

```bash
export FLUXION_JWT_SECRET=<long random string>
```

Опциональные: `POSTGRES_PASSWORD` (default `fluxion`), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_TEAM`, `OLLAMA_HOST` (default `http://host.docker.internal:11434` — Ollama на хосте).

```bash
docker compose -f docker-compose.prod.yml up -d
```

## Smoke-тесты после деплоя

```bash
curl -fsS http://localhost:8000/api/health

# register → login → chat через /api/sessions/{id}/chat
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "smoke@example.com", "password": "smoke-test-123", "name": "Smoke"}' \
  | jq -r .access_token)

curl -s -X POST http://localhost:8000/api/sessions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
```

## Замечания

- PostgreSQL для production: `FLUXION_DB_URL=postgresql+psycopg2://...` (SQLite — только dev)
- Ollama запускается на хосте; контейнер обращается через `host.docker.internal`
- В контейнере без Ollama `/api/health` честно вернёт `"ollama_available": false`
