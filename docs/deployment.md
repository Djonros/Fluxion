# Деплой (Docker)

Production-стек web-приложения: Docker-образ + PostgreSQL + (опционально) Stripe billing.

## Образ

`Dockerfile` — multi-stage на `python:3.12-slim`:

- только runtime-пакеты (`requirements-server.txt`), итоговый образ ~474 MB
- non-root пользователь `fluxion`
- данные — в `/app/data`
- `HEALTHCHECK` — `curl http://localhost:8000/api/health`
- порт `8000`
- `FLUXION_SERVER_MODE=saas` по умолчанию — многопользовательский режим (ниже)

```bash
docker build -t fluxion .
docker run -p 8000:8000 -e FLUXION_JWT_SECRET=$(openssl rand -hex 32) fluxion
```

## Production compose

`docker-compose.prod.yml` — приложение + PostgreSQL 16 (healthcheck `pg_isready`, том `fluxion_pgdata_prod`).

Обязательные переменные (без них compose не запустится):

```bash
export FLUXION_JWT_SECRET=$(openssl rand -base64 48)   # не короче 32 символов
export POSTGRES_PASSWORD=<надёжный пароль>
export FLUXION_CORS_ORIGINS=https://ваш-сайт.example   # адреса фронтенда через запятую
```

Compose сам задаёт `FLUXION_ENV=production` (без надёжного JWT-секрета сервер не
стартует), `FLUXION_SERVER_MODE=saas` и том `fluxion_workspaces_prod` для папок
пользователей.

Опциональные: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`,
`STRIPE_PRICE_TEAM`, `STRIPE_PRICE_ENTERPRISE`, `OLLAMA_HOST` (по умолчанию
`http://host.docker.internal:11434` — Ollama на хосте).

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

## Режим SaaS

В многопользовательском режиме сервер защищает себя и пользователей друг от
друга:

- агент работает **только на чтение** и не запускает тесты (pytest выполняет код
  проекта с правами сервера);
- каждый пользователь видит только свою папку `FLUXION_WORKSPACES_DIR/<id>`;
- серверная индексация RAG отключена: индекс общий для процесса.

Для одного пользователя на своём компьютере (расширение VS Code) используйте
`fluxion-server.bat` — он запускает сервер в режиме `local` только на
`127.0.0.1`.

## Замечания

- PostgreSQL для production: `FLUXION_DB_URL=postgresql+psycopg2://...` (SQLite — только dev)
- Ollama запускается на хосте; контейнер обращается через `host.docker.internal`
- В контейнере без Ollama `/api/health` честно вернёт `"ollama_available": false`
