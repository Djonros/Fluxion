# QLoRA-дообучение

Дообучение Qwen2.5-Coder на ваших примерах (QLoRA) с готовой моделью для чата на выходе. Функция Pro. Нужны видеокарта NVIDIA от 6 ГБ и отдельное окружение Python 3.11/3.12 с CUDA — программа ставит его сама.

Более подробно — `README-TRAIN.md` в корне репозитория.

## В приложении

Страница **«Обучение»** запускает весь пайплайн одной кнопкой (Pro):

1. Если внизу написано «Окружение обучения не найдено» — нажмите
   **«Установить окружение обучения»**: программа проверит Python 3.12 (при
   отсутствии предложит winget-установку) и развернёт venv. В Full-сборке стек
   ставится **офлайн** из `training_pack\wheels`.
2. Укажите датасет `.jsonl` в кодировке UTF-8 кнопкой **Выбрать…**. Каждая
   строка — один пример в одном из форматов:
   - ChatML: `{"messages": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}]}`;
   - ShareGPT: `{"conversations": [{"from": "human", "value": "…"}, {"from": "gpt", "value": "…"}]}`;
   - Alpaca: `{"instruction": "…", "input": "…", "output": "…"}` (`input` и `system` — необязательно);
   - пары `prompt`/`completion`, `prompt`/`response`, `question`/`answer`,
     `вопрос`/`ответ`, `problem`/`solution`, `input`/`output`.

   Рядом с полем видно примерное число примеров. Если их больше ~20 000, а в
   «Макс. примеров» стоит «все», программа предложит взять первые 5 000: весь
   большой файл на одной видеокарте обучается сутками.

   Рядом с полем видно, распознан ли формат; с нераспознанным файлом обучение
   не начнётся, а подсказка назовёт поля, найденные в файле.
3. Выберите пресет (`low` — 6–8 ГБ VRAM, unsloth, последовательности по 1024 токена; `standard` — 8–12 ГБ, peft+trl)
   или загрузите пресет из файла кнопкой **«Загрузить из файла…»**, затем эпохи
   и число примеров. В **«▸ Дополнительные настройки»** — базовая модель,
   название будущей модели и адаптера и флажок **«Собрать готовую модель для
   чата (GGUF)»**; для сборки нужна папка llama.cpp в переменной `LLAMA_CPP_DIR`
   (под флажком программа подсказывает, найдена ли она). Обычно эти настройки
   менять не нужно.
4. Нажмите **«Начать обучение»** — лог пайплайна идёт в окно страницы, **Стоп**
   останавливает после текущего этапа.

По завершении адаптер регистрируется в реестре, а готовая модель копируется в
папку моделей программы и появляется в списке **«Модель»** в чате под названием
из «Дополнительных настроек». Если установлена Ollama, модель создаётся и в ней.
Без `LLAMA_CPP_DIR` сохраняются адаптер и объединённая модель — их можно собрать
в GGUF позже командами ниже.

## Пресеты VRAM

| Путь | VRAM | Библиотека |
|------|------|-----------|
| `low` | ~6 ГБ | unsloth |
| `standard` | 8–12 ГБ | HF transformers + peft + trl |

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

# 7. Добавить модель в программу: «Модели» → «Импорт с диска…» → файл из data/gguf
#    (или скопируйте его в %LOCALAPPDATA%\Fluxion\models).
#    По желанию — в Ollama:
cd data/gguf
ollama create fluxion-coder-python -f Modelfile
```

## Пайплайн данных

- **Источники** — CodeSearchNet, The Stack v2
- **Дедуп** — MinHash
- **Фильтры** — качество, язык, длина
- **Формат** — ChatML JSONL

Модель выбирается в списке «Модель» в чате. Если вы работаете через Ollama,
укажите её имя в `config/config.yaml`:

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
