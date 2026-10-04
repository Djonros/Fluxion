# Конфигурация

Большинству пользователей настраивать ничего не нужно: программа сама выбирает
встроенный движок llama.cpp, а модель скачивается на странице **«Модели»**.
Эта страница — для тех, кто хочет изменить поведение вручную.

## config.yaml

Основной файл — `config/config.yaml`. Другой путь можно задать переменной
`AI_AGENT_CONFIG`.

```yaml
backend: ""            # "" — авто; llama_cpp | ollama | api
gguf_path: ""          # путь к GGUF-модели (llama.cpp); пусто — модель со страницы «Модели»
n_gpu_layers: 0        # сколько слоёв модели на видеокарте; -1 — все (llama.cpp)
language: auto         # auto | ru | en | off — язык ответов
model: qwen2.5-coder:7b-instruct   # имя модели для Ollama
ollama_host: http://localhost:11434

generation:
  temperature: 0.2
  top_p: 0.9
  max_tokens: 2048
  num_ctx: 32768

rag:
  embedding_backend: ""    # "" — авто; llama_cpp | ollama
  embedding_gguf: ""       # путь к GGUF эмбеддера (bge-m3)
  embedding_model: bge-m3
  embedding_device: cpu
  chroma_dir: data/chroma
  chunk_max_chars: 1500
  chunk_overlap: 200
  top_k: 8
  rerank: true

web:
  searxng_url: http://localhost:8080
  max_pages: 3
  timeout: 10
  cache_ttl_hours: 24

paths:
  data_dir: data
  repos_dir: data/repos
```

| Секция | Ключи | Что задают |
|--------|-------|-----------|
| корень | `backend`, `gguf_path`, `n_gpu_layers`, `language`, `model`, `ollama_host` | Движок, модель, видеокарта, язык ответов |
| `generation` | `temperature`, `top_p`, `max_tokens`, `num_ctx` | Параметры генерации |
| `rag` | `embedding_*`, `chroma_dir`, `chunk_*`, `top_k`, `rerank` | Индекс кода проекта |
| `web` | `searxng_url`, `max_pages`, `timeout`, `cache_ttl_hours` | Веб-поиск |
| `paths` | `data_dir`, `repos_dir` | Папки данных |

### Какой движок выбирается

Если `backend` пуст и не задана переменная `FLUXION_BACKEND`:

1. `api` — если задан `FLUXION_API_KEY`;
2. `llama_cpp` — если установлен llama-cpp-python (в сборках он есть всегда).
   Модель может быть ещё не скачана: мастер первичной настройки и страница
   «Модели» предложат её скачать, и она подключится без перезапуска;
3. `ollama` — во всех остальных случаях.

Если основной движок недоступен, программа пробует остальные в порядке
`<выбранный> → ollama → api → llama_cpp`.

## Переменные окружения

Переменные можно задать в системе или в файле `config/.env` (рядом с
`config.yaml`) либо `.env` в корне проекта. Шаблон — `config/.env.example`.
Настоящие переменные окружения важнее значений из `.env`.

### Движок и модели

| Переменная | Описание |
|------------|----------|
| `FLUXION_BACKEND` | `llama_cpp` / `ollama` / `api`; перекрывает `backend` из config.yaml |
| `FLUXION_GGUF_PATH` | Путь к GGUF-модели для llama.cpp |
| `FLUXION_N_GPU_LAYERS` | Слои модели на видеокарте (llama.cpp) |
| `FLUXION_MODELS_DIR` | Папка моделей вместо `%LOCALAPPDATA%\Fluxion\models` |
| `FLUXION_VRAM_BUDGET_GB` | Предел видеопамяти для загруженных моделей, ГБ; 0 или пусто — без ограничения |
| `FLUXION_EMBED_GGUF` | GGUF эмбеддера для индекса кода, если не задан `rag.embedding_gguf` |
| `FLUXION_AGENT_FORMAT` | Формат действий агента: `auto` (по умолчанию), `json`, `text` |
| `OLLAMA_HOST` | Адрес Ollama (`http://localhost:11434`) |
| `MODEL` | Модель Ollama, если в config.yaml не задан `model` |
| `FLUXION_API_KEY` | Ключ облачного API (Pro) |
| `FLUXION_API_BASE_URL` | Адрес OpenAI-совместимого API |
| `FLUXION_API_MODEL` | Модель в API |
| `FLUXION_API_STRUCTURED` | `1` — JSON-схема для действий агента через API (если провайдер поддерживает) |
| `HF_TOKEN` | Токен Hugging Face (эмбеддинги, датасеты, обучение) |

### Веб-поиск

| Переменная | Описание |
|------------|----------|
| `SEARXNG_URL` | Адрес SearXNG; перекрывает `web.searxng_url` |
| `FLUXION_SEARXNG` | `1` — включить расширенный поиск через SearXNG без меню (в приложении — **Правка → Расширенный веб-поиск**) |

По умолчанию работает встроенный веб-поиск, Docker не нужен и не запускается.

### Лицензия, обновления, обучение

| Переменная | Описание |
|------------|----------|
| `FLUXION_LICENSE_FILE` | Путь к файлу активации вместо стандартного |
| `FLUXION_RELEASES_URL` | Адрес, по которому программа проверяет обновления |
| `LLAMA_CPP_DIR` | Папка llama.cpp: нужна, чтобы после обучения собрать готовую модель (GGUF) |
| `FLUXION_OFFLINE_WHEELS` | Папка офлайн-пакета для установки окружения обучения |
| `FLUXION_TORCH_INDEX` | Индекс пакетов PyTorch для онлайн-установки окружения обучения |
| `FLUXION_PYTHON` | Python, которым скрипты сборки и установки движка ставят пакеты |

### Сервер (веб-приложение и расширение VS Code)

| Переменная | Описание |
|------------|----------|
| `FLUXION_SERVER_MODE` | `local` (по умолчанию) или `saas` — многопользовательский режим с ограничениями |
| `FLUXION_ENV` | `production` — сервер не стартует без надёжного `FLUXION_JWT_SECRET` |
| `FLUXION_JWT_SECRET` | Секрет подписи сессий, не короче 32 символов |
| `FLUXION_WORKSPACES_DIR` | Папка проектов пользователей в режиме `saas` |
| `FLUXION_CORS_ORIGINS` | Разрешённые адреса сайтов через запятую |
| `FLUXION_DB_URL` | Пусто — SQLite; `postgresql+psycopg2://...` — PostgreSQL |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Оплата через Stripe |
| `STRIPE_PRICE_PRO`, `STRIPE_PRICE_TEAM`, `STRIPE_PRICE_ENTERPRISE` | Price ID платных планов |

### Для разработчиков и тестов

| Переменная | Описание |
|------------|----------|
| `FLUXION_DISABLE_WEBENGINE` | `1` — без встроенного браузера (тесты без QtWebEngine) |
| `FLUXION_WEBENGINE_DARK` | `0` — не затемнять страницы во встроенном браузере |
| `FLUXION_DESKTOP_SMOKE`, `FLUXION_SMOKE_RAG` | Проверочный запуск сборки без фоновых служб |

## Облачный API

```bash
# Пример: Groq
set FLUXION_BACKEND=api
set FLUXION_API_KEY=gsk_...
set FLUXION_API_BASE_URL=https://api.groq.com/openai/v1
set FLUXION_API_MODEL=llama-3.1-70b-versatile
```

Облачные модели — возможность Pro.

## Continue.dev

Скопируйте `config/continue_config.json` в `~/.continue/config.json`, чтобы
подключить Fluxion к [Continue](https://continue.dev) в VS Code или JetBrains.
