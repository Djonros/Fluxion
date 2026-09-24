# Fluxion — видео «5 минут до локального ИИ» (Phase 16.2, сценарий v2)

- **Длительность:** ровно 5:00
- **Язык:** русская озвучка + английские субтитры (два трека: RU/EN)
- **Формат:** 1920×1080, 30 fps, захват экрана + титры
- **Музыка:** лоу-фай фон, −18 дБ под голосом; финал — тишина + логотип

## Таймкод 0:00 — Хук (40 сек)

**Экран:** тёмный фон, крупный текст. Затем — рабочий стол с открытым чужим
облачным AI-чатом, красная стрелка «твой код → чужой сервер».

**RU (озвучка):**
> Каждый раз, когда ты вставляешь код в облачный ИИ, он покидает твой компьютер.
> Файлы, ключи, корпоративная логика — всё уходит кому-то ещё.
> Fluxion — ассистент, который работает только у тебя. Твоя модель, твой индекс,
> твой GPU. Your code never leaves your machine.

**EN (субтитры):**
> Every time you paste code into a cloud AI, it leaves your computer.
> Files, secrets, business logic — all shipped somewhere else.
> Fluxion is an assistant that runs only on your machine. Your model, your index,
> your GPU. Your code never leaves your machine.

## Таймкод 0:40 — Установка и мастер (50 сек)

**Экран:** браузер → сайт Fluxion → кнопка Download Lite → распаковка 7z → двойной
клик `FluxionBrowserLite.exe` → мастер настройки: чек системы (CPU/GPU-пак),
скачивание GGUF-модели с прогресс-баром, тестовый промпт.

**RU:**
> Скачиваем сборку — сто тридцать мегабайт, без установки. Распаковали, запустили.
> Ни Docker, ни Ollama — мастер сам скачает GGUF-модель с прогресс-баром и проверит,
> что всё работает. Через минуту — главное окно. Всё, что дальше, происходит офлайн.

**EN:**
> Download the build — 130 megabytes, no installer. Unzip, run.
> No Docker, no Ollama — the wizard pulls the GGUF model with a progress bar and
> makes sure everything works. A minute later — the main window. Everything from
> here on is offline.

## Таймкод 1:30 — Чат и RAG (60 сек)

**Экран:** страница «Чат» → вопрос «Объясни этот проект» → метка `[strategy=rag]`.
Затем страница «Проект»: Выбрать… → Проиндексировать → «RAG: 247 чанков» →
возврат в чат, вопрос по конкретной функции, в ответе — источники из кода.

**RU:**
> Начнём с чата. Спрашиваем о проекте — и получаем ответ не из общих знаний, а из
> твоего кода. Открываем «Проект», выбираем папку, жмём «Проиндексировать».
> Fluxion режет код на семантические чанки и складывает в локальную базу.
> Теперь ответы опираются на твой репозиторий — с ссылками на источники.

**EN:**
> Start with chat. Ask about the project — and the answer comes from your code,
> not generic knowledge. Open "Project", pick a folder, hit "Index".
> Fluxion splits the code into semantic chunks and stores them locally.
> Every answer is grounded in your repository — with sources.

## Таймкод 2:30 — Агент (70 сек)

**Экран:** страница «Агент»: задача «добавь docstrings в utils.py и прогони
тесты» → чекбокс записи (Pro) → Запустить: пошаговый лог (read → edit → run_tests),
затем git diff с подсветкой. Параллельно — чекбокс «Агент: доступ к файлам» в чате.

**RU:**
> Дальше — агент. Описываем задачу: добавить докстринги и прогнать тесты.
> Он сам читает файлы, правит код и запускает pytest — каждый шаг виден на экране.
> Перед каждой записью создаётся git-чекпоинт: что-то пошло не так — откат в один
> клик. Запись файлов открывается Pro-ключом, чтение и анализ — бесплатны.

**EN:**
> Next — the agent. Describe the task: add docstrings and run the tests.
> It reads files, edits code and runs pytest — every step is on screen.
> A git checkpoint is created before each write: anything goes wrong — one-click
> rollback. Writing requires a Pro key; reading and analysis are free.

## Таймкод 3:40 — Дообучение (60 сек)

**Экран:** страница «Обучение»: «Установить окружение обучения» → прогресс venv →
датасет .jsonl → пресет low → «Начать обучение» → бегущий лог лося → готово: модель
появляется в списке моделей чата. Титр: «Full-сборка: обучение без интернета».

**RU:**
> И главное — дообучение. Указываем свой датасет, выбираем пресет под шесть
> гигабайт видеопамяти — и запускаем QLoRA прямо здесь. Лог пайплайна перед
> глазами: адаптер, мердж, GGUF, и вот она — твоя личная модель в списке чата.
> В Full-сборке весь стек обучения ставится офлайн: ни одного запроса в интернет.

**EN:**
> And the main dish — fine-tuning. Point it at your dataset, pick the 6-GB preset
> and start QLoRA right here. The pipeline log is in front of you: adapter, merge,
> GGUF — and there it is, your personal model in the chat list.
> The Full build installs the whole training stack offline: zero internet requests.

## Таймкод 4:40 — Финал (20 сек)

**Экран:** затемнение → логотип Fluxion (∞) → слоган. Без музыки.

**RU:**
> Fluxion. Локальный ИИ для вайб-кодинга. The derivative of your vibe.

**EN (титр, 5 сек):**
> Fluxion. Local AI for vibe coding.
> **The derivative of your vibe.**

---

## Чеклист съёмки

- [ ] 0:00 хук без логотипа, сразу боль
- [ ] 0:40 реальная установка Lite на чистой машине (или VM)
- [ ] 0:40 сцена мастера (v2): чек системы → прогресс скачивания GGUF → тестовый промпт
- [ ] 1:30 метка `[strategy=rag]` крупно, 2 секунды статично
- [ ] 2:30 git diff с подсветкой — ключевой кадр доверия
- [ ] 3:40 лог обучения в реальном времени, ускорение ×8
- [ ] 4:40 логотип + слоган, тишина
- [ ] Субтитры EN — жёстким таймкодом по блокам выше
- [ ] Итоговый рантайм 4:55–5:00
