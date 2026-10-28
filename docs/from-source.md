# Запуск из исходников

Для разработчиков: Fluxion из исходного кода, батники Windows, CLI и
API-сервер. Готовую программу ставит `FluxionSetup.exe` — см. [Установка](install.md).

## Требования

- Готовые сборки: **только Windows x64** — движок llama.cpp уже внутри exe
- Запуск из исходников: **Python 3.11+** + `pip install -r requirements.txt -r requirements-llamacpp.txt`
- Ollama — **опционально**, как альтернативный бэкенд (`backend: ollama`)
- Docker — опционально, только для расширенного веб-поиска через SearXNG
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
| `fluxion-update.bat` | Для разработчика: обновление исходников из архива с резервной копией, откатом и пересборкой exe. Архив с той же или более старой версией отклоняется (`--force` — принять). Готовую программу обновляет `FluxionSetup.exe` |
| `fluxion-build.bat` | Пересборка exe из текущего кода, без обновления (`--lite`); если установлен Inno Setup (`winget install JRSoftware.InnoSetup --scope user`) — и установщик `dist\artifacts\FluxionSetup-<версия>.exe` с `.sha256` |
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

Веб-поиск работает сразу встроенным способом. Расширенный поиск через SearXNG в
Docker включается в программе: меню **Правка → «Расширенный веб-поиск»**; для CLI и
сервера — `powershell -File scripts\setup_searxng.ps1`.

## Ручная установка (любая ОС)

```bash
# 1. Клонировать репозиторий
git clone https://github.com/djonros/fluxion.git
cd fluxion

# 2. Установить зависимости (включая llama.cpp)
pip install -r requirements.txt -r requirements-llamacpp.txt

# 3. Запустить программу — мастер первого запуска предложит скачать модель
python -m desktop_browser

# CLI (модель — скачанная в программе или gguf_path в config/config.yaml):
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

    Команда `/status` покажет состояние движка, модели, индекса и веб-поиска.

=== "Сервер"

    ```bash
    python -m uvicorn server.app:create_app --factory --port 8765
    curl http://localhost:8765/api/health
    ```

## CLI

Запустите `fluxion.bat` или `python -m cli.app` — тот же движок в REPL-форме:

| Команда | Описание |
|---------|----------|
| `/help` | Список команд |
| `/status` | Состояние модели, индекса и веб-поиска |
| `/model` | Текущая модель |
| `/index [путь]` | Проиндексировать папку |
| `/search <запрос>` | Поиск по проиндексированному коду |
| `/rag on\|off\|clear` | Принудительно включить или выключить поиск по коду |
| `/route <запрос>` | Показать маршрут вопроса без ответа |
| `/web <запрос>` | Веб-поиск |
| `/webclear` | Очистить кэш веб-поиска |
| `/agent [--write] [--iter N] <задача>` | Агент; `--write` — правка файлов (Pro) |
| `/rollback [путь]` | Отменить правки последнего запуска агента |
| `/adapters list\|info\|register\|delete\|switch` | Адаптеры LoRA |
| `/license status\|activate <ключ или файл>\|deactivate` | Лицензия |
| `/theme dark\|light` | Тема оформления |
| `/clear` | Очистить историю диалога |
| `/exit` | Выход |
