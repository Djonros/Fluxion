# QLoRA-дообучение

Дообучение Qwen2.5-Coder на Python-датасетах с экспортом в GGUF для Ollama. Требует отдельный Python 3.11/3.12 venv с CUDA.

Более подробно — `README-TRAIN.md` в корне репозитория.

## В приложении

Страница **«Обучение»** запускает весь пайплайн одной кнопкой (Pro):

1. Если внизу написано «Окружение обучения не найдено» — нажмите
   **«Установить окружение обучения»**: программа проверит Python 3.12 (при
   отсутствии предложит winget-установку) и развернёт venv. В Full-сборке стек
   ставится **офлайн** из `training_pack\wheels`.
2. Укажите датасет `.jsonl` (ChatML, поле `messages`) кнопкой **Выбрать…**.
3. Выберите пресет (`low` — 6 ГБ VRAM, unsloth; `standard` — 8–12 ГБ, peft+trl),
   эпохи и параметры; чекбокс «Экспорт GGUF + ollama create» требует
   `LLAMA_CPP_DIR`.
4. Нажмите **«Начать обучение»** — лог пайплайна идёт в окно страницы, **Стоп**
   останавливает после текущего этапа.

По завершении адаптер регистрируется в реестре, а созданная Ollama-модель
автоматически появляется в списке моделей чата.

## Пресеты VRAM

| Путь | VRAM | Библиотека |
|------|------|-----------|
| `low` | ~6 ГБ | unsloth |
| peft+trl | 8–12 ГБ | HF transformers + peft + trl |

## Пайплайн

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

## Пайплайн данных

- **Источники** — CodeSearchNet, The Stack v2
- **Дедуп** — MinHash
- **Фильтры** — качество, язык, длина
- **Формат** — ChatML JSONL

После создания модели в Ollama переключитесь на неё:

```yaml
model: fluxion-coder-python
```

## Маркетплейс адаптеров

Локальный реестр дообученных адаптеров (`finetune/marketplace.py`) хранится в `data/lora_registry/index.json`. Управление через CLI-команду `/adapters`:

| Команда | Действие |
|---------|----------|
| `/adapters` или `/adapters list` | список адаптеров (`*` — активная модель) |
| `/adapters info <name>` | метаданные адаптера |
| `/adapters register <name> [--model M] [--path P] [--quant Q] [описание]` | зарегистрировать после обучения (дефолты — из `QLoRASettings`) |
| `/adapters delete <name> [--files]` | удалить запись (`--files` — также удалить LoRA-файлы) |
| `/adapters switch <name>` | переключить `settings.model` и backend на Ollama-модель адаптера |

## Проверка качества

Прогоните [HumanEval / MBPP eval](eval.md) до и после дообучения, чтобы измерить эффект.
