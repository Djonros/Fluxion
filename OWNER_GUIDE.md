# Fluxion — Руководство владельца

Инструкция по запуску и управлению продуктом: деплой сервера, домен и HTTPS,
Stripe-биллинг, смена цен, публикация документации, бэкапы и обновления.
Секреты в этот файл не кладём — только названия переменных окружения.

---

## 1. Что вы управляете

| Компонент | Где живёт | Назначение |
|-----------|-----------|------------|
| Локальное ядро (CLI/агент/RAG) | машина пользователя | бесплатное open-source ядро |
| Pro-лицензии (десктоп) | офлайн, без сервера | одноразовые Ed25519-ключи (раздел 6) |
| Web-приложение (`server/`) | ваш сервер (VPS) | аккаунты, сессии, тарифы, биллинг |
| Ollama + модель | ваш сервер или API-бэкенд | инференс для web-чата |
| Stripe | dashboard.stripe.com | приём платежей |
| Документация | GitHub Pages | публичный сайт |

Схема: пользователь → ваш домен (HTTPS) → Caddy/Nginx → контейнер `fluxion-app` (:8000) → PostgreSQL; Ollama на хосте или облачный API.

---

## 2. Деплой сервера на VPS

### 2.1. Выбор сервера

- **Бюджетный старт (рекомендую)**: VPS 2–4 ГБ RAM без GPU + **API-бэкенд**
  (OpenRouter/Groq) — модель в облаке, платите за токены. `FLUXION_API_KEY`.
- **Полностью свой инференс**: VPS/сервер с ≥16 ГБ RAM (CPU-инференс Qwen-7B
  медленный) или GPU. Ollama на хосте, контейнер достучится сам через
  `host.docker.internal`.
- ОС: Ubuntu 22.04/24.04.

### 2.2. Подготовка VPS

```bash
ssh root@<IP>
adduser fluxion && usermod -aG sudo,docker fluxion
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable

# Docker
curl -fsSL https://get.docker.com | sh

# Код (после публикации репо; до неё — scp/rsync папки проекта)
git clone https://github.com/djonros/fluxion.git /opt/fluxion
cd /opt/fluxion
```

### 2.3. Конфигурация — `.env` рядом с compose

```bash
cd /opt/fluxion
cp config/.env.example .env
nano .env
```

Обязательное и важное:

```ini
# Сгенерируйте: openssl rand -hex 32
FLUXION_JWT_SECRET=<длинная случайная строка>

# База (пароль придумайте; compose создаст БД сам)
POSTGRES_PASSWORD=<сильный пароль>

# Инференс — вариант А: API-бэкенд
FLUXION_BACKEND=api
FLUXION_API_KEY=sk-or-...
FLUXION_API_BASE_URL=https://openrouter.ai/api/v1
FLUXION_API_MODEL=qwen/qwen2.5-coder-7b-instruct

# Инференс — вариант Б: Ollama на хосте (раскомментируйте)
# OLLAMA_HOST=http://host.docker.internal:11434

# Stripe (см. раздел 4)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_TEAM=price_...
STRIPE_PRICE_ENTERPRISE=price_...
```

### 2.4. Запуск и проверка

```bash
cd /opt/fluxion
docker compose -f docker-compose.prod.yml --env-file .env up -d --build

docker compose -f docker-compose.prod.yml ps        # оба healthy
curl -fsS http://localhost:8000/api/health          # {"status": "ok", ...}

# Smoke: регистрация + создание сессии
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "smoke@example.com", "password": "smoke-test-123", "name": "Smoke"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST http://localhost:8000/api/sessions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
```

Логи: `docker compose -f docker-compose.prod.yml logs -f app`.

---

## 3. Домен и доступ из интернета (HTTPS)

### 3.1. DNS

У регистратора домена (например, `fluxion.app`) создайте A-запись:

```
@     A     <IP вашего VPS>
www   A     <IP вашего VPS>
docs  A     <IP вашего VPS>     # если документация будет на своём поддомене
```

### 3.2. Caddy — HTTPS автоматически (рекомендую)

```bash
# на VPS
apt install -y caddy
nano /etc/caddy/Caddyfile
```

```
fluxion.app {
    reverse_proxy localhost:8000
}
```

```bash
systemctl reload caddy
```

Caddy сам получит сертификат Let's Encrypt и будет продлевать его.
Проверка: `curl -fsS https://fluxion.app/api/health`.

Альтернатива — Nginx + certbot (`apt install nginx certbot python3-certbot-nginx`;
`certbot --nginx -d fluxion.app`), но конфиг руками длиннее.

### 3.3. Firewall

Извне открыты только 80/443. Порт 8000 и 5432 наружу не открывать
(`ufw deny 8000` при необходимости) — Caddy проксирует локально.

---

## 4. Stripe — приём платежей

### 4.1. Первый запуск (test-режим)

1. Аккаунт на [dashboard.stripe.com](https://dashboard.stripe.com).
2. **Developers → API keys**: скопируйте `sk_test_...` → `STRIPE_SECRET_KEY`.
3. **Product catalog → Products**: создайте продукты Pro / Team / Enterprise,
   каждому — рекуррентная цена $19 / $49 / $199 в месяц.
   Из цены скопируйте ID `price_...` → `STRIPE_PRICE_PRO` / `_TEAM` / `_ENTERPRISE`.
4. **Developers → Webhooks → Add endpoint**:
   - URL: `https://fluxion.app/api/billing/webhook`
   - События: `checkout.session.completed`,
     `customer.subscription.updated`, `customer.subscription.deleted`,
     `invoice.payment_failed`
   - Signing secret `whsec_...` → `STRIPE_WEBHOOK_SECRET`.
5. Пересоздайте контейнер: `docker compose -f docker-compose.prod.yml --env-file .env up -d`.
6. Тест: зарегистрируйтесь на своём сайте, `GET /api/billing/plans` — тарифы на месте;
   `POST /api/billing/checkout {"plan_id": "pro"}` → перейдите по ссылке и оплатите
   тестовой картой `4242 4242 4242 4242` (любой срок/CVC).
   После оплаты `GET /api/billing/subscription` покажет план `pro`, `active`.

### 4.2. Переход в live

1. В dashboard переключитесь **Live mode** (переключатель Test/Live).
2. Live-ключи `sk_live_...` и создайте live-продукты/цены и live-webhook —
   это *отдельные* ID, не тестовые.
3. Обновите `.env` live-значениями, перезапустите контейнер.

---

## 5. Как менять цены и лимиты

Цена живёт в **двух местах**, их надо синхронизировать:

| Место | Что задаёт | Файл |
|-------|-----------|------|
| `PLANS` в `server/billing/plans.py` | цифру в API (`GET /api/billing/plans`), лимиты запросов/мин | репозиторий |
| Stripe Dashboard → Products | реальное списание денег | Stripe |

Порядок смены цены (пример: Pro $19 → $24):

1. В Stripe создайте **новую Price** ($24) у того же продукта — старую
   заархивируйте (существующие подписки продолжат списания по старой цене,
   новые пойдут по новой).
2. `STRIPE_PRICE_PRO` в `.env` → новый `price_...`.
3. В `server/billing/plans.py` поправьте `price_monthly_usd=24` (это то, что
   сайт показывает пользователям).
4. Лимиты (запросов/мин по категориям `chat`/`crud`/`usage`/`billing`/`default`)
   — только в `plans.py`, поле `limits`.
5. Тесты: `python -m pytest tests/test_w5_billing.py -q`.
6. Деплой заново: `docker compose -f docker-compose.prod.yml --env-file .env up -d --build`.

Чтобы цена менялась без релиза кода — вынесите `price_monthly_usd` в env
(задача на будущее; сейчас значение статическое в `plans.py`).

---

## 6. Продажа Pro-лицензий (десктоп, офлайн)

Монетизация локального ядра — **одноразовые ключи** (например, $49 perpetual,
сроковые — по договорённости). Проверка полностью офлайн: подпись Ed25519,
сервер и телеметрии нет. Веб-биллинг Stripe (раздел 4) — отдельная система
для хостинг-версии.

### 6.1. Ключевая пара

Пара уже сгенерирована: `keys/private.pem` (приватный, только у вас) и
`keys/public.pem`. Проверка, что приватный ключ на месте и валиден:

```bash
python -c "from licensing.issuer import load_private_key; load_private_key(); print('ok')"
```

`keys/` не публикуется (проверьте `.gitignore`). Новая пара — только при
ротации скомпрометированного ключа:

```python
# python - <<EOF
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

key = Ed25519PrivateKey.generate()
Path("keys/private.pem").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)
pub = key.public_key().public_bytes(
    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
)
import base64
print(base64.b64encode(pub).decode())  # вставить в licensing/verifier.py -> PUBLIC_KEY_B64
```

**Важно:** после ротации ключа все ранее выданные ключи перестанут проходить
проверку — выпускайте их заново. Публичный ключ живёт в `licensing/verifier.py`
(`PUBLIC_KEY_B64`, DER SPKI в base64) и попадает в релиз.

### 6.2. Выпуск ключа покупателю

```bash
# бессрочная (perpetual)
python -m licensing.issuer --email customer@example.com

# годовая
python -m licensing.issuer --email customer@example.com --expires 2027-08-20

# в файл сразу
python -m licensing.issuer --email customer@example.com > key.txt
```

Ключ отправляется покупателю (email/бот); тот активирует его в CLI:
`/license activate <ключ>`. Проверить выданный ключ можно так же, как это
делает приложение:

```bash
python -c "from licensing.verifier import verify_license; print(verify_license('<ключ>'))"
```

### 6.3. Учёт и правила

- Ведите список выданных ключей (email → дата → срок) хотя бы в таблице;
  сами ключи не восстанавливаются из публичного ключа.
- `plan=pro` — единственный платный план локальной лицензии (`free` тоже
  существует для тестов).
- Истёкшая лицензия просто переводит пользователя на free — блокировок нет.
- Приватный ключ не кладите ни в репозиторий, ни в релизные сборки, ни в
  облачные бэкапы без шифрования.

---

## 7. Сайт документации в интернете

Документация (MkDocs) публикуется на GitHub Pages:

```bash
# локально, в папке проекта (нужен pip install mkdocs-material)
python -m mkdocs gh-deploy   # соберёт site/ и запушит в ветку gh-pages
```

Затем на GitHub: **Settings → Pages → Source: ветка `gh-pages`**.
Сайт появится на `https://djonros.github.io/fluxion/`.

Свой домен для документации: DNS `docs.fluxion.app → A <IP>` или CNAME на
`djonros.github.io`; в Settings → Pages укажите custom domain
`docs.fluxion.app`, GitHub выдаст HTTPS. Локальная проверка перед публикацией:
`python -m mkdocs build --strict` (0 warnings).

---

## 8. Эксплуатация

### Бэкапы (главное — база)

```bash
# ежедневный дамп, cron в 3:00
crontab -e
0 3 * * * docker exec fluxion-db-prod pg_dump -U fluxion fluxion | gzip > /var/backups/fluxion-$(date +\%F).sql.gz
```

Восстановление: `gunzip -c dump.sql.gz | docker exec -i fluxion-db-prod psql -U fluxion fluxion`.

### Обновление на новую версию

```bash
cd /opt/fluxion
git pull
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

Перед большим обновлением — дамп базы (команда выше).

### Мониторинг

- `GET /api/health` — статус приложения, Ollama, RAG, web (ставьте
  uptime-проверку: UptimeRobot / Better Stack — бесплатно).
- `docker compose -f docker-compose.prod.yml logs -f app` — логи;
  ошибки и usage пишутся в базу (таблица usage logs).
- Место на диске: `docker system df`, образы ~0.5 ГБ + том Postgres.

### Частые проблемы

| Симптом | Причина / решение |
|---------|-------------------|
| `/api/health` → `ollama_available: false` | Ollama не запущен на хосте или `OLLAMA_HOST` неверный; либо переключитесь на API-бэкенд |
| 429 у пользователей | Лимиты тарифа (`plans.py`); это норма — проверьте, какой тариф |
| Webhook не доходит | URL недоступен из интернета (DNS/HTTPS), неверный `STRIPE_WEBHOOK_SECRET`, порт 8000 закрыт прокси |
| Оплата прошла, план не сменился | Смотрите `logs app` на `_handle_event`; проверьте события вебхука в dashboard |
| Контейнер БД не healthy | Неверный `POSTGRES_PASSWORD` при первом старте (том уже инициализирован) — поправьте или пересоздайте том |

---

## 9. Чек-лист запуска в прод (сводно)

1. [ ] VPS создан, ufw: 22/80/443
2. [ ] Репо склонировано, `.env` заполнен (`FLUXION_JWT_SECRET`, `POSTGRES_PASSWORD`, инференс)
3. [ ] `docker compose -f docker-compose.prod.yml up -d --build`, health OK
4. [ ] DNS A-запись → IP, Caddy настроен, `https://<домен>/api/health` отвечает
5. [ ] Stripe: продукты/цены, webhook, test-оплата `4242…` прошла, план сменился
6. [ ] Live-ключи включены, тестовый юзер не имеет доступа
7. [ ] Cron-бэкап базы настроен, uptime-мониторинг добавлен
8. [ ] `mkdocs gh-deploy` — документация опубликована
