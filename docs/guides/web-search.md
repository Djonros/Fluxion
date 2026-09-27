# Веб-поиск (SearXNG)

Fluxion использует SearXNG в Docker как метапоисковик и trafilatura для извлечения чистого текста. Результаты кэшируются в SQLite.

## Установка

Десктоп-приложение запускает SearXNG само, если установлен Docker Desktop.
Для CLI и API-сервера:

```powershell
powershell -File scripts/setup_searxng.ps1
```

Скрипт создаёт контейнер `fluxion-searxng` с настройками, где включён
JSON-формат ответов: без него SearXNG отвечает программе ошибкой 403.

## Проверка

```bash
curl "http://localhost:8080/search?q=python&format=json"
```

## Использование

```
❯ /web python 3.12 new features
❯ /webclear        # очистить кэш веб-поиска
```

Router может сам выбрать маршрут `WEB` для вопросов о свежем (после cutoff модели) — превью через `/route`.

## Кэш

Результаты поиска хранятся в SQLite-кэше, чтобы не дублировать запросы. Очистка:

```
❯ /webclear
```

## Конфигурация

```yaml
web:
  searxng_url: http://localhost:8080
  max_pages: 3     # сколько страниц читать на результат
```

Env: `SEARXNG_URL=http://localhost:8080`.
