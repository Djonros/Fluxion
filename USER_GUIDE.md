# Fluxion — Руководство пользователя

Полная инструкция: установка, чат и агент, поиск по своему коду, веб-поиск,
VS Code, веб-приложение, дообучение, оценка качества и Pro-лицензия.

---

## 1. Что такое Fluxion

Локальный AI-ассистент для программирования на Python:

- работает на **вашей машине** — код и переписка не уходят в чужое облако;
- отвечает на вопросы **по вашему проекту** (индексирует код и ищет по нему);
- умеет **автономно решать задачи** агентом: читать/править файлы, запускать
  тесты, делать git-коммиты;
- умеет искать в **интернете** (если включён веб-поиск);
- можно **дообучить** под свой стиль кода и оценить качество на тестах.

Требования: Python 3.11+, [Ollama](https://ollama.com), ~8 ГБ ОЗУ (для
qwen2.5-coder:7b). Docker — опционально (веб-поиск). GPU — опционально
(ускоряет генерацию и обучение).

---

## 2. Установка

### 2.1. Windows (проще всего)

Скачайте/склонируйте папку проекта и запустите батники:

```
1. fluxion-deploy.bat    — установит зависимости, Ollama-модель, всё проверит
                           (интерактивное меню; запускается один раз)
2. fluxion.bat           — запуск CLI (ежедневно)
```

Опционально:

| Батник | Что делает |
|--------|-----------|
| `fluxion-web.bat` | поднимает SearXNG в Docker → веб-поиск |
| `fluxion-server.bat` | поднимает API-сервер на `:8765` (для VS Code) |
| `fluxion-vscode.bat` | устанавливает расширение VS Code |
| `fluxion-setup.bat` | лёгкая установка без проверок |

### 2.2. Любая ОС (ручная установка)

```bash
git clone https://github.com/djonros/fluxion.git
cd fluxion

# зависимости
pip install -r requirements.txt

# модель
ollama pull qwen2.5-coder:7b-instruct

# запуск
python -m cli.app
```

### 2.3. Перенос на флешке

Проект портативен: скопируйте папку целиком, на другом ПК выполните
`fluxion-deploy.bat` (Windows) или шаги 2.2 — конфиги используют относительные пути.

---

## 3. CLI — чат и команды

Запуск: `fluxion.bat` (Windows) или `python -m cli.app`.

Просто печатайте вопрос — Fluxion сам выберет стратегию (прямой ответ /
поиск по коду / интернет):

```
Fluxion — local Python AI assistant
Type a question, or /help for commands. /exit to quit.

❯ How do I read a CSV file in Python?
[strategy=direct]
To read a CSV file...

❯ How does the Router classify queries in this project?
[strategy=rag | rag(5 sources)]
The Router in orchestrator/router.py...
```

### Все команды

| Команда | Что делает |
|---------|-----------|
| `/help` | список команд |
| `/status` | статус Ollama, модели, RAG, веб-поиска |
| `/index [путь]` | индексировать папку проекта для поиска по коду |
| `/search <запрос>` | поиск по индексированному коду (без генерации) |
| `/rag on\|off\|clear` | включить/выключить ответ с поиском по коду |
| `/route <запрос>` | показать, какая стратегия будет выбрана |
| `/web <запрос>` | поиск в интернете |
| `/webclear` | очистить кэш веб-поиска |
| `/model` | информация о текущей модели |
| `/clear` | очистить историю диалога |
| `/agent [--write] <задача>` | агент: автономно решить задачу (`--write` — Pro) |
| `/adapters list\|info\|switch …` | управление LoRA-адаптерами |
| `/license status\|activate\|deactivate` | управление Pro-лицензией |
| `/theme dark\|light` | переключить тёмную/светлую тему интерфейса |
| `/exit` | выход |

### Оформление

Интерфейс использует фирменную фиолетово-бирюзовую палитру Fluxion. По умолчанию
включена тёмная тема; переключение сохраняется между запусками:

```text
/theme light     ← светлая тема
/theme dark      ← тёмная тема
/theme           ← показать текущую
```

Также тему можно задать перед первым запуском переменной `FLUXION_THEME=light`
или `FLUXION_THEME=dark`. Выбор хранится локально в `data/ui-theme.json`.

### Типичный день

```
/index .                 ← 1 раз на проект: индексация
/rag on                  ← ответы с опорой на ваш код
❯ Где обрабатываются ошибки веб-поиска?
❯ Добавь retry с backoff в web/fetcher.py
/agent --write «покрой fetcher тестами и закоммить»   ← агент сделает сам
```

---

## 4. Агент — автономные задачи

`/agent <задача>` — агент планирует и выполняет шаги: читает файлы, ищет
(grep), правит, запускает тесты, смотрит git-статус, при успехе коммитит.

```
/agent Найти все TODO в проекте и составить список по файлам
/agent --write Исправь падающий тест tests/test_w4_usage.py и закоммить
```

- **Без `--write` агент только читает** — ничего не изменит.
- `--write` требует **Pro-лицензию** (см. раздел 8); read-режим бесплатен.
- С `--write` перед каждой записью создаётся **авто-checkpoint**
  (git stash): откатить — `git stash pop` / см. историю.
- Завершает работу сам, инструментом `finish` с итоговым отчётом.

---

## 5. Веб-поиск (опционально)

```bash
# Windows
fluxion-web.bat

# любая ОС: SearXNG в Docker
docker run -d -p 8080:8080 searxng/searxng
```

Проверка: `curl "http://localhost:8080/search?q=python&format=json"`.
Дальше — `/web <запрос>` в CLI, или агент сам решит воспользоваться поиском.

---

## 6. VS Code расширение

1. Запустите сервер: `fluxion-server.bat` (или
   `python -m uvicorn server.app:create_app --factory --port 8765`).
2. Установите расширение: `fluxion-vscode.bat` (или откройте `extension/` в VS Code → F5).

Команды (Ctrl+Shift+P):

- **Fluxion: Open Chat** — чат со стримингом в боковой панели
- **Fluxion: Run Agent** — запустить агента над открытой папкой
- **Fluxion: Index Project** — индексировать workspace
- **Fluxion: Server Status** — проверить сервер

---

## 7. Смена модели / облачный бэкенд

По умолчанию — локальная модель через Ollama. Можно переключить в
`config/.env` (см. `config/.env.example`):

| Бэкенд | Когда | Переменные |
|--------|-------|-----------|
| **Ollama** (по умолчанию) | локально, приватно, бесплатно | — |
| **API** (OpenRouter/Groq/Together/vLLM) | нет своей машины/быстрее; **Pro** | `FLUXION_BACKEND=api`, `FLUXION_API_KEY`, `FLUXION_API_BASE_URL`, `FLUXION_API_MODEL` |
| **llama.cpp** | прямой GGUF-файл без сервера; **Pro** | `FLUXION_BACKEND=llama_cpp`, `FLUXION_GGUF_PATH` |

При недоступности основного бэкенда Fluxion автоматически падает на следующий
(ollama → api → llama_cpp). Без Pro-лицензии API/llama.cpp-бэкенды заменяются
на Ollama с предупреждением при старте.

---

## 8. Pro-лицензия (десктоп)

Ядро Fluxion бесплатно (Apache 2.0). Продвинутые возможности — **Pro**
(одноразовый ключ, проверка офлайн через Ed25519, без сервера):

| | Free | Pro |
|---|:---:|:---:|
| Чат, RAG, веб-поиск, read-only агент, Ollama | ✅ | ✅ |
| Агент с записью (`/agent --write`) | — | ✅ |
| QLoRA-дообучение | — | ✅ |
| API / llama.cpp бэкенды | — | ✅ |

```text
/license status                  ← текущий план и срок
/license activate <ключ>         ← активация (проверка подписи офлайн)
/license deactivate              ← удалить активацию
```

Ключ хранится локально (`data/license.key`), срок действия входит в подпись.
Подробнее — `docs/guides/licensing.md`.

---

## 9. Веб-приложение (аккаунт, сессии, тарифы)

Если вы пользуетесь хостингом владельца (например, `https://fluxion.app`):

1. **Регистрация**: `POST /api/auth/register` (email + пароль) → токен.
2. **Чат**: создайте сессию и общайтесь — история сохраняется, ответы
   стримятся (SSE).
3. **Тарифы** (`GET /api/billing/plans`): Free (локально, всегда),
   Pro / Team / Enterprise — выше лимиты запросов; Enterprise — организации
   с командой и аналитикой. Апгрейд — из личного кабинета (Stripe Checkout).
4. **Организации (Enterprise)**: владелец создаёт орг по email-домену —
   коллеги с тем же доменом присоединяются автоматически при регистрации.

Self-hosted веб-версия: `docker compose -f docker-compose.prod.yml up -d`,
см. `docs/deployment.md`.

---

## 10. Дообучение под свой код (опционально, Pro)

Требует Python 3.11/3.12 venv + GPU (от 6 ГБ). Полный путь:

```bash
python3.11 -m venv venv-train && venv-train\Scripts\activate
pip install -r requirements-train.txt

# 1. Датасет
python -m data_pipeline.run codesearchnet --limit 5000 --output data/instruction_train.jsonl

# 2. Обучение (unsloth — 6 ГБ VRAM)
python -m finetune.train_unsloth --preset low --dataset data/instruction_train.jsonl

# 3. Merge + GGUF + модель в Ollama
python -m finetune.merge --adapter data/lora_output/adapter
python -m finetune.export_gguf --model data/merged_model --llama-cpp-dir <llama.cpp>
cd data/gguf && ollama create fluxion-coder-python -f Modelfile
```

Готовые адаптеры (в т.ч. фреймвор-специфичные) — через `/adapters list` /
`/adapters switch <имя>` (см. `docs/guides/finetune.md`).

---

## 11. Оценка качества (eval)

```bash
# стандартные бенчмарки
python -m eval.runner --benchmark humaneval --limit 20
python -m eval.runner --benchmark mbpp --limit 20

# свои проектные задачи — JSON-файл (формат: eval/tasks.sample.json)
python -m eval.runner --benchmark custom --tasks-path my_tasks.json
```

Результат: pass@k — доля задач, решённых с первого/к-го раза. Сравнивайте
базовую и дообученную модель до/после обучения.

---

## 12. Если что-то не работает

| Проблема | Решение |
|----------|---------|
| `Backend not available` | не запущен Ollama (`ollama serve`) или модель не скачана (`ollama pull qwen2.5-coder:7b-instruct`) |
| `/web` не работает | SearXNG не поднят (`fluxion-web.bat`, проверка `localhost:8080`) |
| `/rag` отвечает не по делу | переиндексируйте: `/rag clear` → `/index .` |
| Агент не меняет файлы | агент в режиме read-only — добавьте `--write` (нужна Pro-лицензия, раздел 8) |
| `invalid license key` при активации | ключ скопирован не целиком или с опечаткой; повторите `/license activate <ключ>` |
| `license expired` | срок лицензии истёк — обновите ключ у владельца и активируйте заново |
| Стартует Ollama, хотя настроен API-бэкенд | API/llama.cpp — Pro-фичи; без Pro используется Ollama (раздел 8) |
| VS Code «Server Status» красный | сначала `fluxion-server.bat`; порт 8765 |
| 429 Too Many Requests (веб) | исчерпан лимит тарифа — подождите минуту или апгрейдните план |
| Медленная генерация | GPU для Ollama, или API-бэкенд (раздел 7) |

Вопросы и идеи — [GitHub Discussions](https://github.com/djonros/fluxion/discussions);
баги — [Issues](https://github.com/djonros/fluxion/issues).
