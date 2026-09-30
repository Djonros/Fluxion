# Fluxion

<p align="center">
  <img src="assets/banner.png" alt="Fluxion" width="560">
</p>

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)

**Локальный AI-ассистент для вайб-кодинга на Python**

> ⬇️ Скачать программу, модели и пресеты обучения — **[djonros.github.io/fluxion](https://djonros.github.io/fluxion/)**, документация — **[/docs](https://djonros.github.io/fluxion/docs/)**.
> 📘 Пользователям: полное руководство по установке и работе — **[USER_GUIDE.md](USER_GUIDE.md)**.
> Владельцам (деплой сервера, цены, домен) — **[OWNER_GUIDE.md](OWNER_GUIDE.md)**.

Fluxion — это полностью локальный AI-ассистент на базе Qwen2.5-Coder-7B: движок llama.cpp встроен в приложение, ничего устанавливать не нужно. RAG по вашему коду, живой веб-поиск, умная маршрутизация запросов, ReAct-агент и пайплайн QLoRA-дообучения — всё на вашем компьютере, без облака. **No Docker. No Ollama. No cloud. One exe.**

```
              ┌──────────── CLI (prompt_toolkit + rich) ────────────┐
              │   /index  /search  /web  /route  /rag  /status       │
              └───────────────────────┬──────────────────────────────┘
                                      │
                            ┌─────────▼─────────┐
                            │   Assistant       │  фасад
                            └─────────┬─────────┘
                                      │
                            ┌─────────▼─────────┐
                            │     Router        │  DIRECT/RAG/WEB
                            └──┬──────┬───────┬─┘
                               │      │       │
                    ┌───────────▼┐ ┌───▼────┐ ┌▼──────────┐
                    │ llama.cpp/ │ │  RAG   │ │ Web Search│
                    │ Ollama/API │ │ Engine │ │ (SearXNG) │
                    └────────────┘ └───┬────┘ └───────────┘
                                    │
                             ┌──────▼──────┐
                             │  ChromaDB   │  bge-m3 + tree-sitter
                             └─────────────┘
```

## Возможности

- **Multi-model** — llama.cpp (GGUF, движок встроен в сборку), Ollama, OpenAI-compatible API (OpenRouter, Groq, Together)
- **RAG по коду** — AST-aware чанкинг (tree-sitter), эмбеддинги bge-m3 (GGUF), ChromaDB
- **Умная маршрутизация** — rule-based Router: DIRECT / RAG / WEB / RAG_THEN_WEB
- **Веб-поиск** — SearXNG в Docker + trafilatura для чистого текста + SQLite кэш
- **ReAct-агент** — 12 инструментов: `list_files`, `read_file` (по диапазонам строк), `grep`, `search_code` (поиск по смыслу через RAG), `write_file`, `edit_file`, `run_tests`, `web_search`, `git_status`, `git_diff`, `git_commit`, `finish`. Со встроенным llama.cpp и Ollama действия модели проверяются JSON-схемой — маленькая модель не может выдать неверный формат. Правки проверяются тестами до завершения
- **Git integration** — снимок рабочей папки перед первой правкой (ваши незакоммиченные изменения не трогаются), откат `/rollback`, коммит только изменённых агентом файлов
- **FastAPI сервер** — REST API: `/api/chat` (streaming SSE), `/api/agent/run`, `/api/rag/index`, `/api/health`
- **VS Code extension** — чат-сайдбар, agent mode, индексация проекта
- **QLoRA-дообучение** — unsloth (6 ГБ) или HF peft+trl (8-12 ГБ), экспорт в GGUF для Ollama
- **Пайплайн данных** — CodeSearchNet, The Stack v2, MinHash-дедуп, фильтры, ChatML JSONL
- **Eval** — HumanEval / MBPP / custom project-specific pass@k

## Быстрый старт

### Требования

- Готовые сборки (Windows x64): только exe — движок llama.cpp уже внутри
- Запуск из исходников: Python 3.11+, `pip install -r requirements.txt -r requirements-llamacpp.txt`
- Ollama (опционально, как альтернативный бэкенд)
- Docker (опционально, для веб-поиска через SearXNG)
- Python 3.11/3.12 venv (опционально, для QLoRA-дообучения)

### Развертывание (Windows — батники)

| Батник | Что делает |
|--------|-----------|
| **`fluxion-setup.bat`** | Установка из исходников: виртуальное окружение, зависимости, встроенный движок llama.cpp, по желанию — сервер. Запускается один раз, повторный запуск пропускает установленное |
| **`fluxion-desktop.bat`** | Запуск приложения (то же, что `FluxionBrowserLite.exe`): чат, агент, RAG, веб-поиск, модели |
| `fluxion.bat` | CLI в терминале (чат, RAG, агент) |
| `fluxion-server.bat` | API-сервер на `127.0.0.1:8765` для расширения VS Code |
| `fluxion-vscode.bat` | Установка расширения VS Code |
| `fluxion-test.bat` | Тесты по группам и проверка агента на вашей модели |
| `fluxion-update.bat` | Обновление из архива с резервной копией, откатом и пересборкой exe |
| `fluxion-release.bat` | Выпуск новой версии на GitHub: тесты, коммит, тег, релиз со сборками (для владельца) |
| `issue-key.bat` | Выпуск лицензионных ключей (для владельца) |

```
Первый запуск:
  1. fluxion-setup.bat      → установка
  2. fluxion-desktop.bat    → программа; модель — страница «Модели»

Дополнительно:
  fluxion-vscode.bat + fluxion-server.bat → расширение VS Code
  fluxion.bat               → CLI
```

Веб-поиск (SearXNG в Docker) программа запускает сама, если установлен
Docker Desktop. Для CLI и сервера: `powershell -File scripts\setup_searxng.ps1`.

### Установка (ручная — любая ОС)

```bash
# 1. Клонировать репозиторий
git clone <repo-url> fluxion
cd fluxion

# 2. Установить зависимости (включая llama.cpp)
pip install -r requirements.txt -r requirements-llamacpp.txt

# 3. Запустить — мастер первого запуска сам скачает GGUF-модель
python -m cli.app

# API сервер (опционально):
pip install -r server/requirements.txt
python -m uvicorn server.app:create_app --factory --port 8765
```

### Перенос на флешке

Проект полностью портативен. Скопируйте папку целиком, на новом компьютере:

```
1. Вставить флешку
2. Запустить fluxion-setup.bat (установит зависимости и движок)
3. Запустить fluxion-desktop.bat
```

Конфиги используют относительные пути — хардкод путей отсутствует.

### Базовое использование

```
Fluxion  —  local Python AI assistant
Type a question, or /help for commands. /exit to quit.

❯ How do I read a CSV file in Python?
[strategy=direct]
To read a CSV file...

/index .
Searching... Indexing: .
Indexed 247 chunks from .

/rag on
RAG override ON

❯ How does the Router classify queries?
[strategy=rag | rag(5 sources)]
The Router in orchestrator/router.py...
```

### Команды CLI

| Команда | Описание |
|---------|----------|
| `/help` | Список команд |
| `/status` | Статус Ollama, модели, RAG, SearXNG |
| `/index [path]` | Индексировать директорию в ChromaDB |
| `/search <query>` | Поиск по индексированному коду |
| `/rag on\|off\|clear` | Управление RAG-аугментацией |
| `/route <query>` | Превью маршрутизации (без генерации) |
| `/web <query>` | Веб-поиск через SearXNG |
| `/webclear` | Очистить кэш веб-поиска |
| `/model` | Информация о текущей модели |
| `/clear` | Очистить историю диалога |
| `/agent [--write] <task>` | ReAct-агент: автономное решение задачи (`--write` включает запись файлов) |
| `/exit` | Выход |

## Веб-поиск (опционально)

```bash
# Запустить SearXNG в Docker
powershell -File scripts/setup_searxng.ps1

# Проверить
curl "http://localhost:8080/search?q=python&format=json"
```

## QLoRA-дообучение (опционально)

Требует отдельный Python 3.11/3.12 venv с CUDA.

```bash
# 1. Создать venv
python3.11 -m venv venv-train
source venv-train/bin/activate  # Linux/Mac
venv-train\Scripts\activate     # Windows

# 2. Установить зависимости
pip install -r requirements-train.txt

# 3. Собрать датасет
python -m data_pipeline.run codesearchnet --limit 5000 --output data/instruction_train.jsonl

# 4. Обучить (unsloth для 6 ГБ)
python -m finetune.train_unsloth --preset low --dataset data/instruction_train.jsonl

# 5. Объединить адаптер
python -m finetune.merge --adapter data/lora_output/adapter

# 6. Экспорт в GGUF
python -m finetune.export_gguf --model data/merged_model --llama-cpp-dir /path/to/llama.cpp

# 7. Создать модель в Ollama
cd data/gguf
ollama create fluxion-coder-python -f Modelfile
```

## Eval (HumanEval / MBPP / Custom)

```bash
python -m eval.runner --benchmark humaneval --limit 20 --samples 1
python -m eval.runner --benchmark mbpp --limit 20 --temperature 0.2
python -m eval.runner --benchmark custom --tasks-path eval/tasks.sample.json
```

## Конфигурация

Основной конфиг — `config/config.yaml`:

```yaml
backend: ""            # "" (авто) | llama_cpp | ollama | api
gguf_path: ""          # путь к GGUF (бэкенд llama_cpp)
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
  chroma_dir: data/chroma
  top_k: 8

web:
  searxng_url: http://localhost:8080
  max_pages: 3
```

Env-переменные — `config/.env.example`.

## Multi-model

Fluxion поддерживает 3 backend для инференса:

| Backend | Когда использовать | Настройка |
|---------|-------------------|-----------|
| **llama.cpp** (в сборках по умолчанию) | Без внешних серверов: GGUF грузится прямо в приложение | `FLUXION_BACKEND=llama_cpp`, `FLUXION_GGUF_PATH=...` (или страница «Модели») |
| **Ollama** | Уже есть Ollama-сервер с моделями | `FLUXION_BACKEND=ollama`, `ollama pull qwen2.5-coder:7b-instruct` |
| **API** | Cloud модели (OpenRouter, Groq, Together, vLLM) | `FLUXION_BACKEND=api`, `FLUXION_API_KEY=sk-...` |

```bash
# Пример: переключить на Groq (cloud)
export FLUXION_BACKEND=api
export FLUXION_API_KEY=gsk_...
export FLUXION_API_BASE_URL=https://api.groq.com/openai/v1
export FLUXION_API_MODEL=llama-3.1-70b-versatile

# BackendFactory автоматически выберет лучший доступный backend
# Fallback chain: <выбранный> → ollama → api → llama_cpp
```

## FastAPI сервер

```bash
# Установка
pip install -r server/requirements.txt

# Запуск
python -m uvicorn server.app:create_app --factory --port 8765

# Или батник
fluxion-server.bat
```

| Endpoint | Method | Описание |
|----------|--------|----------|
| `/api/health` | GET | Статус, модель, движок, RAG, web |
| `/api/chat` | POST | Чат (streaming SSE или JSON) |
| `/api/agent/run` | POST | Запуск ReAct-агента |
| `/api/rag/index` | POST | Индексация директории |

## VS Code extension

См. `extension/README.md` для установки. Команды:

- `Fluxion: Open Chat` — чат-сайдбар со streaming
- `Fluxion: Run Agent` — запуск агента из VS Code
- `Fluxion: Index Project` — индексация workspace
- `Fluxion: Server Status` — проверка сервера

## Архитектура

```
fluxion/
├── core/              # Конфигурация, backends: Ollama, API, llama.cpp, BackendFactory
├── rag/               # RAG: чанкинг, эмбеддинги, ChromaDB, индексация, поиск
├── web/               # Веб-поиск: SearXNG, fetcher, кэш, пайплайн
├── orchestrator/      # Router, PromptBuilder, Assistant, ReAct-агент, git_helper
├── server/            # FastAPI: chat (SSE), agent, rag, health
├── extension/         # VS Code extension: chat, agent, indexing
├── data_pipeline/     # Сбор данных, фильтры, дедуп, форматтер, клонер репо
├── finetune/          # QLoRA: конфиг, датасет, обучение, merge, GGUF-экспорт
├── eval/              # HumanEval / MBPP / custom pass@k
├── licensing/         # Ed25519-лицензии: issue/verify/store + Pro feature gates
├── cli/               # REPL, команды, рендеринг (prompt_toolkit + rich)
├── config/            # config.yaml, .env, repos.yaml, continue_config.json
├── desktop_browser/   # Десктоп-приложение (PySide6): чат, агент, браузер, модели, обучение
├── scripts/           # Сборка exe и сайта, установка llama.cpp, SearXNG
├── website/           # Страница загрузки (GitHub Pages)
├── presets/training/  # Пресеты обучения (JSON)
├── tests/             # pytest; запуск по группам — fluxion-test.bat
├── docs/              # Документация (mkdocs); docs/internal — внутренние заметки
├── requirements.txt   # Инференс/RAG/веб/CLI
└── requirements-train.txt  # Обучение (Python 3.11/3.12)
```

## Тесты

```bash
python -m pytest tests/ -q
```

На Windows удобнее `fluxion-test.bat`: тесты по группам (агент, ядро, сервер,
десктоп, лицензии, обучение) со сводкой; группа пропускается, если не установлены
её пакеты. Там же — проверка агента на вашей модели (бенчмарк из 36 задач,
`eval/agent_bench`). CI прогоняет тесты на Ubuntu и Windows, Python 3.11–3.13.

## Continue.dev интеграция

Скопируйте `config/continue_config.json` в `~/.continue/config.json` для интеграции с [Continue](https://continue.dev) — расширением VS Code / JetBrains.

## Сообщество

- **Вопросы, идеи, showcейс проектов** — [GitHub Discussions](https://github.com/djonros/fluxion/discussions) (Q&A + feature requests)
- **Баги и фичи** — [Issues](https://github.com/djonros/fluxion/issues); шаблоны отчёта — в `.github/ISSUE_TEMPLATE/`
- **Хотите внести вклад?** — см. [CONTRIBUTING.md](CONTRIBUTING.md); стартовые задачи помечены [`good first issue`](https://github.com/djonros/fluxion/labels/good%20first%20issue)
- **Безопасность** — пишите на <djonros@gmail.com> напрямую, публичные issue не открывайте

## Лицензия

Apache License 2.0

Copyright 2024-2026 **Djonros** `<djonros@gmail.com>`

See [LICENSE](LICENSE) for the full text.

---

**Fluxion** — by Djonros
