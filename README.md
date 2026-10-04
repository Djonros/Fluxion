# Fluxion

<p align="center">
  <img src="assets/banner.png" alt="Fluxion" width="560">
</p>

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-informational.svg)

**Помощник для Python-кода, который работает на вашем компьютере.**

> ⬇️ **Скачать** программу, модели и пресеты обучения — [djonros.github.io/fluxion](https://djonros.github.io/fluxion/)
> 📘 **Документация** — [djonros.github.io/fluxion/docs](https://djonros.github.io/fluxion/docs/), руководство пользователя — [USER_GUIDE.md](USER_GUIDE.md)
> 🛠 **Владельцу** (сборка, выпуск, лицензии, сервер, сайт) — [OWNER_GUIDE.md](OWNER_GUIDE.md)

Fluxion — чат о коде, агент, поиск по проекту и дообучение модели в одной
программе для Windows. Модель (Qwen2.5-Coder) работает на встроенном движке
llama.cpp: код проекта не покидает компьютер, а интернет нужен только чтобы
скачать программу и модель. Ollama и Docker не нужны.

## Возможности

| Возможность | Бесплатно | Pro |
|---|:---:|:---:|
| Чат о коде с учётом проекта | ✅ | ✅ |
| Поиск по коду проекта по смыслу (RAG) | ✅ | ✅ |
| Веб-поиск: встроенный; расширенный через SearXNG в Docker — по желанию | ✅ | ✅ |
| Агент: находит файлы, читает код, запускает тесты, объясняет ошибки | ✅ | ✅ |
| Агент правит файлы сам и проверяет правки тестами; откат одной командой | — | ✅ |
| Модели: Qwen2.5-Coder 7B и 3B, эмбеддер BGE-M3 | ✅ | ✅ |
| Расширенный каталог моделей: 7B в точности Q8_0, 14B, 32B | — | ✅ |
| Дообучение QLoRA на своих примерах, пресеты обучения из файла | — | ✅ |
| Облачные модели через API (OpenRouter, Groq, Together, vLLM) | — | ✅ |
| CLI, расширение VS Code, локальный API-сервер | ✅ | ✅ |

Лицензия Pro активируется в программе: **Помощь → Лицензия…** — файлом ключа
или вставкой ключа. Подробнее — [docs/guides/licensing.md](docs/guides/licensing.md).

## Быстрый старт

### Готовая программа

1. Скачайте Fluxion Lite на [странице загрузки](https://djonros.github.io/fluxion/)
   и распакуйте архив `.7z`.
2. Запустите `FluxionBrowserLite.exe`.
3. Мастер первичной настройки предложит скачать модель (около 4,7 ГБ, с
   докачкой) — после загрузки чат сразу готов к работе.

Нужны Windows 10 или 11 (64 бит). Видеокарта не обязательна: модель работает
на процессоре, с видеокартой NVIDIA — быстрее.

### Из исходников (Windows)

```
1. fluxion-setup.bat     — окружение .venv, зависимости, встроенный движок llama.cpp
2. fluxion-desktop.bat   — запуск программы; модель — страница «Модели»
```

| Батник | Назначение |
|--------|-----------|
| `fluxion-setup.bat` | Установка; повторный запуск пропускает установленное |
| `fluxion-desktop.bat` | Программа (то же, что `FluxionBrowserLite.exe`) |
| `fluxion.bat` | CLI в терминале |
| `fluxion-server.bat` | API-сервер на `127.0.0.1:8765` для расширения VS Code |
| `fluxion-vscode.bat` | Установка расширения VS Code |
| `fluxion-test.bat` | Тесты по группам и проверка агента на вашей модели |
| `fluxion-update.bat` | Обновление из архива: резервная копия, проверки, пересборка exe, откат |
| `fluxion-build.bat` | Пересборка exe из текущего кода, без обновления (`--lite`, `--full`) |
| `fluxion-release.bat` | Выпуск новой версии на GitHub (для владельца) |
| `issue-key.bat` | Выпуск лицензионных ключей (для владельца) |

### Из исходников (любая ОС)

```bash
git clone https://github.com/djonros/fluxion.git
cd fluxion
pip install -r requirements.txt -r requirements-llamacpp.txt
python -m desktop_browser          # программа
python -m cli.app                  # или CLI
```

Модель скачайте на странице «Модели» в программе или укажите путь к GGUF в
`config/config.yaml` (`gguf_path`). Подробнее — [docs/install.md](docs/install.md).

## CLI

```
❯ Как прочитать CSV в Python?
[strategy=direct]
...
❯ /index .
Indexed 247 chunks from .
❯ /agent найди, почему падает tests/test_cart.py
```

| Команда | Что делает |
|---------|-----------|
| `/help` | Список команд |
| `/status` | Состояние модели, индекса и веб-поиска |
| `/model` | Текущая модель |
| `/index [путь]` | Проиндексировать папку для поиска по коду |
| `/search <запрос>` | Поиск по проиндексированному коду |
| `/rag on\|off\|clear` | Принудительно включить или выключить поиск по коду в ответах |
| `/route <запрос>` | Показать, как будет обработан вопрос (без ответа) |
| `/web <запрос>` | Веб-поиск |
| `/webclear` | Очистить кэш веб-поиска |
| `/agent [--write] [--iter N] <задача>` | Агент; `--write` — правка файлов (Pro) |
| `/rollback [путь]` | Отменить правки последнего запуска агента |
| `/adapters list\|info\|register\|delete\|switch` | Адаптеры LoRA после дообучения |
| `/license status\|activate <ключ или файл>\|deactivate` | Лицензия |
| `/theme dark\|light` | Тема оформления |
| `/clear` | Очистить историю диалога |
| `/exit` | Выход |

## Подробнее

| Тема | Где |
|------|-----|
| Установка, батники, ручная установка | [docs/install.md](docs/install.md) |
| Настройки: config.yaml, переменные окружения, выбор движка | [docs/configuration.md](docs/configuration.md) |
| Модели и каталог Pro | [docs/guides/models.md](docs/guides/models.md) |
| Чат, поиск по коду, веб-поиск, агент | [docs/guides/](docs/guides/) |
| Дообучение QLoRA, пресеты, обучение из командной строки | [docs/guides/finetune.md](docs/guides/finetune.md) |
| Оценка моделей и бенчмарк агента | [docs/guides/eval.md](docs/guides/eval.md), [eval/agent_bench/README.md](eval/agent_bench/README.md) |
| API-сервер и расширение VS Code | [docs/api.md](docs/api.md), [docs/guides/vscode.md](docs/guides/vscode.md) |
| Решение проблем | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Что изменилось | [CHANGELOG.md](CHANGELOG.md), [RELEASE_NOTES_RU.md](RELEASE_NOTES_RU.md) |

## Как устроено

```
              ┌──── Программа (PySide6) · CLI · VS Code → API-сервер ────┐
              └───────────────────────────┬──────────────────────────────┘
                                ┌─────────▼─────────┐
                                │ Assistant / Router │  прямой ответ, код, веб
                                └──┬───────┬───────┬─┘
                     ┌─────────────▼┐  ┌───▼────┐  ┌▼──────────────────────┐
                     │ llama.cpp     │  │  RAG   │  │ Веб-поиск: встроенный │
                     │ (Ollama, API) │  │ Chroma │  │ (+ SearXNG по желанию)│
                     └───────────────┘  └────────┘  └───────────────────────┘
                                ┌─────────────────┐
                                │ ReAct-агент      │  12 инструментов, JSON-схема,
                                └─────────────────┘  снимок проекта перед правкой
```

```
fluxion/
├── desktop_browser/   # Программа (PySide6): чат, агент, модели, обучение, браузер
├── core/              # Настройки, движки (llama.cpp, Ollama, API), каталог моделей
├── orchestrator/      # Router, Assistant, ReAct-агент, git-снимки
├── rag/               # Индекс кода: чанкинг tree-sitter, эмбеддинги, ChromaDB
├── web/               # Веб-поиск, загрузка страниц, кэш
├── finetune/          # QLoRA: настройки, пресеты, обучение, merge, GGUF
├── data_pipeline/     # Сбор и очистка датасетов
├── licensing/         # Лицензии Ed25519: выпуск, проверка, активация
├── cli/               # CLI (prompt_toolkit + rich)
├── server/            # FastAPI: чат, агент, индексация, авторизация, оплата
├── extension/         # Расширение VS Code
├── eval/              # HumanEval / MBPP / свои задачи, бенчмарк агента
├── website/           # Страница загрузки (GitHub Pages)
├── presets/training/  # Пресеты обучения
├── scripts/           # Сборка exe и сайта, установка движка, SearXNG
├── config/            # config.yaml, .env.example
├── docs/              # Документация (mkdocs)
└── tests/             # pytest; на Windows — fluxion-test.bat
```

## Тесты

```bash
python -m pytest tests/ -q
```

На Windows удобнее `fluxion-test.bat`: тесты по группам со сводкой, группа
пропускается, если не установлены её пакеты; там же — проверка агента на вашей
модели. CI прогоняет тесты на Ubuntu и Windows, Python 3.11–3.13.

## Сообщество

- **Вопросы и идеи** — [GitHub Discussions](https://github.com/djonros/fluxion/discussions)
- **Ошибки** — [Issues](https://github.com/djonros/fluxion/issues), шаблоны в `.github/ISSUE_TEMPLATE/`
- **Участие в разработке** — [CONTRIBUTING.md](CONTRIBUTING.md)
- **Безопасность** — пишите на <djonros@gmail.com>, не открывайте публичные issue

## Лицензия

Apache License 2.0 — см. [LICENSE](LICENSE).

Copyright 2024-2026 **Djonros** `<djonros@gmail.com>`
