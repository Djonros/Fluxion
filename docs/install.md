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
2. Запустить fluxion-setup.bat (установит зависимости и движок)
3. Запустить fluxion-desktop.bat
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
