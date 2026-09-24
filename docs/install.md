# Установка

## Готовые сборки (Windows x64)

[:material-download: Скачать Lite (~130 МБ)](../download/FluxionBrowser-Lite-win64-latest.7z){ .md-button .md-button--primary }
[:material-download: Скачать Full — офлайн-обучение (~2.9 ГБ)](../download/FluxionBrowser-Full-win64-latest.7z){ .md-button }

| Сборка | Что внутри |
|--------|-----------|
| **FluxionBrowser-Lite-win64.7z** | Один exe: чат, RAG, веб-поиск, агент. Распакуйте и запускайте `FluxionBrowserLite.exe` |
| **FluxionBrowser-Full-win64.7z** | Lite + `training_pack\wheels` (torch cu126 + unsloth-стек). Обучение ставится кнопкой на странице «Обучение» **без интернета** — нужен только Python 3.12 |

Для QLoRA-обучения в любой сборке нужен Python 3.12 и NVIDIA GPU (6–12 ГБ VRAM).

## Требования

- Готовые сборки: **только Windows x64** — движок llama.cpp уже внутри exe
- Запуск из исходников: **Python 3.11+** + `pip install -r requirements.txt -r requirements-llamacpp.txt`
- Ollama — **опционально**, как альтернативный бэкенд (`backend: ollama`)
- Docker — опционально, для веб-поиска через SearXNG
- Python 3.12 venv — опционально, для QLoRA-дообучения

## Windows — батники

| Батник | Что делает |
|--------|-----------|
| **`fluxion-deploy.bat`** | Полное развертывание из исходников: Python, зависимости, модель. Интерактивное меню: CLI / сервер / оба / тесты. |
| `fluxion.bat` | Запуск CLI REPL (чат, RAG, агент) |
| `fluxion-server.bat` | Запуск FastAPI сервера на `:8765` |
| `fluxion-web.bat` | Установка и запуск SearXNG в Docker (веб-поиск) |
| `fluxion-vscode.bat` | Установка VS Code extension |
| `fluxion-setup.bat` | Базовая установка зависимостей + модели (облегчённый deploy) |

```
Первый запуск:
  1. fluxion-deploy.bat     → выберите режим в меню
  2. fluxion-web.bat        → (опц.) веб-поиск через SearXNG
  3. fluxion-vscode.bat     → (опц.) VS Code extension

Повседневный запуск:
  fluxion.bat               → CLI
  fluxion-server.bat        → API сервер для extension
```

## Ручная установка (любая ОС)

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

GGUF-модель можно задать и вручную в `config/config.yaml` (`backend: llama_cpp` +
`gguf_path: <путь>`) или скачать на странице «Модели». Уже имеющиеся модели Ollama
импортируются без перекачки: страница «Модели» → «Импорт из Ollama».

## Перенос на флешке

Проект полностью портативен. Скопируйте папку целиком, на новом компьютере:

```
1. Вставить флешку
2. Запустить fluxion-deploy.bat (установит Python-зависимости и модель)
3. Запустить fluxion.bat
```

Конфиги используют относительные пути — хардкод путей отсутствует.

## Проверка установки

=== "CLI"

    ```
    fluxion.bat
    ❯ /status
    ```

    Команда `/status` покажет состояние движка, модели, RAG и SearXNG.

=== "Сервер"

    ```bash
    python -m uvicorn server.app:create_app --factory --port 8765
    curl http://localhost:8765/api/health
    ```
