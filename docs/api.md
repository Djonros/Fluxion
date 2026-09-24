# API сервер

FastAPI-сервер: REST + SSE streaming, auth (JWT), проекты, сессии, usage и Stripe billing.

## Запуск

```bash
pip install -r server/requirements.txt
python -m uvicorn server.app:create_app --factory --port 8765

# или батник
fluxion-server.bat
```

## Core endpoints

| Endpoint | Method | Описание |
|----------|--------|---------|
| `/api/health` | GET | Статус, модель, движок, RAG, web |
| `/api/chat` | POST | Чат (streaming SSE или JSON) |
| `/api/agent/run` | POST | Запуск ReAct-агента |
| `/api/rag/index` | POST | Индексация директории |

### Чат со стримингом

```bash
curl -N -X POST http://localhost:8765/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I read a CSV file in Python?", "stream": true}'
```

## Auth — `/api/auth`

| Endpoint | Method | Описание |
|----------|--------|---------|
| `/api/auth/register` | POST | Регистрация → JWT-пара (201) |
| `/api/auth/login` | POST | Логин → JWT-пара |
| `/api/auth/refresh` | POST | Обновление access-токена |
| `/api/auth/me` | GET | Профиль текущего пользователя |

```bash
TOKEN=$(curl -s -X POST http://localhost:8765/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "..."}' | jq -r .access_token)

curl http://localhost:8765/api/auth/me -H "Authorization: Bearer $TOKEN"
```

## Projects — `/api/projects`

| Endpoint | Method | Описание |
|----------|--------|---------|
| `/api/projects` | POST | Создать проект (201) |
| `/api/projects` | GET | Список проектов |
| `/api/projects/{id}` | GET | Получить проект |
| `/api/projects/{id}` | PATCH | Обновить |
| `/api/projects/{id}` | DELETE | Удалить (204) |

## Sessions — `/api/sessions`

Мультиюзерские чат-сессии с историей.

| Endpoint | Method | Описание |
|----------|--------|---------|
| `/api/sessions` | POST | Создать сессию (201); auto-title при первом сообщении |
| `/api/sessions` | GET | Список сессий |
| `/api/sessions/{id}` | GET | Получить сессию |
| `/api/sessions/{id}` | PATCH | Обновить (title и др.) |
| `/api/sessions/{id}` | DELETE | Удалить (204) |
| `/api/sessions/{id}/messages` | GET | История сообщений |
| `/api/sessions/{id}/chat` | POST | Сообщение в сессию |

## Usage — `/api/usage`

| Endpoint | Method | Описание |
|----------|--------|---------|
| `/api/usage` | GET | Сводка использования по дням |
| `/api/usage/breakdown` | GET | Разбивка по типам запросов |

## Billing — `/api/billing`

Stripe-подписки: Free / Pro / Team / Enterprise.

| Endpoint | Method | Описание |
|----------|--------|---------|
| `/api/billing/plans` | GET | Доступные планы |
| `/api/billing/subscription` | GET | Текущая подписка |
| `/api/billing/checkout` | POST | Stripe Checkout Session |
| `/api/billing/cancel` | POST | Отмена подписки |
| `/api/billing/webhook` | POST | Stripe webhook (подпись проверяется через `STRIPE_WEBHOOK_SECRET`) |

## Organizations — `/api/organizations`

Enterprise-тир: организации с SSO-lite — новые пользователи с email
в домене организации автоматически становятся её участниками. Участники
организации, чей владелец имеет активную enterprise-подписку, наследуют
её лимиты (поэлементный максимум при личной платной подписке).

| Endpoint | Method | Описание |
|----------|--------|---------|
| `/api/organizations` | POST | Создать организацию (создатель = owner) |
| `/api/organizations` | GET | Список моих организаций |
| `/api/organizations/{id}` | GET | Информация об организации (участникам) |
| `/api/organizations/{id}/members` | GET | Участники с ролями (участникам) |
| `/api/organizations/{id}/members/{user_id}` | DELETE | Исключить участника (только owner) |
| `/api/organizations/{id}/usage` | GET | Аналитика usage по участникам (только owner) |

## Rate limits

| Tier | crud rpm | chat rpm |
|------|----------|----------|
| Free | 100 | 30 |
| Pro | 400 | 120 |
| Team | 1000 | 300 |
| Enterprise | 2000 | 600 |

Превышение → `429` с заголовком `Retry-After`.
