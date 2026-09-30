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
3. Выберите пресет (`low` — 6 ГБ VRAM, unsloth; `standard` — 8–12 ГБ, peft+trl)
   или загрузите пресет из файла кнопкой **«Загрузить из файла…»**, затем эпохи
   и параметры; чекбокс «Экспорт GGUF + ollama create» требует
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

## Пресеты из файла

Готовые пресеты можно скачать на сайте Fluxion (раздел «Пресеты обучения») или
взять из папки `presets/training` в репозитории. На странице «Обучение» нажмите
**«Загрузить из файла…»**: программа выставит тип обучения, число эпох,
ограничение на число примеров и базовую модель из файла. Выбор встроенного
пресета в списке отменяет файл. Нужна версия Fluxion 0.11 или новее.

Пресет — JSON-файл:

```json
{
  "format": "fluxion-training-preset",
  "version": 1,
  "id": "my-preset",
  "name": "Мой пресет",
  "description": "Для чего он подходит",
  "trainer": "low",
  "min_vram_gb": 6,
  "base_model": "Qwen/Qwen2.5-Coder-7B-Instruct",
  "max_samples": 500,
  "settings": {
    "lora_r": 32,
    "lora_alpha": 64,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
    "max_seq_length": 2048,
    "num_train_epochs": 2,
    "learning_rate": 0.0002
  }
}
```

| Поле | Значения |
|------|----------|
| `trainer` | `low` — unsloth, от 6 ГБ; `standard` — HF peft+trl, 8–12 ГБ |
| `base_model` | необязательно; только `Qwen/Qwen2.5-Coder-1.5B-Instruct`, `-3B-Instruct` или `-7B-Instruct` |
| `max_samples` | необязательно; сколько примеров датасета взять |
| `lora_r` / `lora_alpha` | 4–256 / 4–512 |
| `lora_dropout` | 0–0,5 |
| `target_modules` | из `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj` |
| `max_seq_length` | 256–8192 |
| `num_train_epochs` | 1–20 |
| `per_device_train_batch_size` | 1–16 |
| `gradient_accumulation_steps` | 1–128 |
| `learning_rate` | 0,000001–0,01 |
| `warmup_ratio`, `weight_decay` | 0–0,5 |
| `lr_scheduler_type` | `cosine`, `linear`, `constant` |

Программа принимает только эти поля и значения в указанных пределах. Базовая
модель ограничена списком намеренно: скрипты обучения загружают её с
`trust_remote_code=True`, и чужой репозиторий в пресете, скачанном из
интернета, мог бы выполнить свой код на вашем компьютере.

Из командной строки файл пресета принимают оба скрипта обучения:
`python -m finetune.train_unsloth --dataset data.jsonl --settings-file preset.json`.
