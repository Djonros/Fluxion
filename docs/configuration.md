# Конфигурация

## config.yaml

Основной конфиг — `config/config.yaml`:

```yaml
backend: ""            # "" (авто) | llama_cpp | ollama | api
gguf_path: ""          # путь к GGUF (бэкенд llama_cpp)
n_gpu_layers: 0        # слои на GPU (бэкенд llama_cpp)
language: auto         # auto | ru | en — язык ответов
model: qwen2.5-coder:7b-instruct   # имя модели (бэкенд ollama)

generation:
  temperature: 0.2
  max_tokens: 2048
  num_ctx: 32768

rag:
  embedding_backend: ""    # "" (авто) | llama_cpp | ollama
  embedding_gguf: ""       # путь к GGUF эмбеддера (bge-m3)
  embedding_model: bge-m3
  embedding_device: cpu
  chroma_dir: data/chroma
  top_k: 8

web:
  searxng_url: http://localhost:8080
  max_pages: 3
```

| Секция | Ключи | Описание |
|--------|-------|---------|
| root | `backend`, `gguf_path`, `n_gpu_layers`, `language`, `model`, `ollama_host` | Выбор бэкенда, модель, язык ответов |
| `generation` | `temperature`, `max_tokens`, `num_ctx` | Параметры генерации |
| `rag` | `embedding_backend`, `embedding_gguf`, `embedding_model`, `chroma_dir`, `top_k` | Эмбеддинги, хранилище ChromaDB, число источников |
| `web` | `searxng_url`, `max_pages` | Адрес SearXNG и лимит страниц |

## Env-переменные

Шаблон — `config/.env.example` (скопируйте в `config/.env`).

### Backend и инференс

| Переменная | Описание |
|------------|----------|
| `OLLAMA_HOST` | Адрес Ollama (`http://localhost:11434`) |
| `MODEL` | Модель по умолчанию (бэкенд ollama) |
| `FLUXION_BACKEND` | `llama_cpp` / `ollama` / `api` (по умолчанию — авто-детект) |
| `FLUXION_API_KEY` | Ключ для API-backend |
| `FLUXION_API_BASE_URL` | Base URL OpenAI-compatible API |
| `FLUXION_API_MODEL` | Имя модели в API |
| `FLUXION_GGUF_PATH` | Путь к GGUF для llama.cpp backend |
| `FLUXION_N_GPU_LAYERS` | Число слоёв на GPU (llama.cpp) |
| `HF_TOKEN` | Токен Hugging Face (эмбеддинги, датасеты, обучение) |
| `SEARXNG_URL` | Адрес SearXNG для веб-поиска |

### Веб-приложение

| Переменная | Описание |
|------------|----------|
| `FLUXION_DB_URL` | Пусто — SQLite (dev); `postgresql+psycopg2://...` — PostgreSQL (prod) |
| `FLUXION_JWT_SECRET` | Секрет JWT. **Обязательно смените в production!** |
| `STRIPE_SECRET_KEY` | Ключ Stripe (billing) |
| `STRIPE_WEBHOOK_SECRET` | Секрет webhook'а Stripe |
| `STRIPE_PRICE_PRO`, `STRIPE_PRICE_TEAM` | Price ID платных планов |

## Multi-model

Fluxion поддерживает 3 backend для инференса:

| Backend | Когда использовать | Настройка |
|---------|-------------------|-----------|
| **llama.cpp** (в сборках по умолчанию) | Без внешних серверов: GGUF грузится прямо в приложение | `FLUXION_BACKEND=llama_cpp`, `FLUXION_GGUF_PATH=...` (или страница «Модели») |
| **Ollama** | Уже есть Ollama-сервер с моделями | `FLUXION_BACKEND=ollama`, `ollama pull qwen2.5-coder:7b-instruct` |
| **API** | Cloud-модели (OpenRouter, Groq, Together, vLLM) | `FLUXION_BACKEND=api`, `FLUXION_API_KEY=sk-...` |

```bash
# Пример: переключить на Groq (cloud)
export FLUXION_BACKEND=api
export FLUXION_API_KEY=gsk_...
export FLUXION_API_BASE_URL=https://api.groq.com/openai/v1
export FLUXION_API_MODEL=llama-3.1-70b-versatile

# BackendFactory автоматически выберет лучший доступный backend
# Fallback chain: <выбранный> → ollama → api → llama_cpp
```

## Continue.dev интеграция

Скопируйте `config/continue_config.json` в `~/.continue/config.json` для интеграции с [Continue](https://continue.dev) — расширением VS Code / JetBrains.
