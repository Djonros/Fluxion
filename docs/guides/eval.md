# Eval — HumanEval / MBPP / Custom

Измерение качества генерации кода: pass@k на стандартных бенчмарках и
проектных задачах.

## Запуск

```bash
python -m eval.runner --benchmark humaneval --limit 20 --samples 1
python -m eval.runner --benchmark mbpp --limit 20 --temperature 0.2
```

## Параметры

| Параметр | Описание |
|----------|---------|
| `--benchmark` | `humaneval`, `mbpp` или `custom` |
| `--limit` | Число задач (для быстрых прогонов) |
| `--samples` | Число сэмплов на задачу (для pass@k) |
| `--temperature` | Температура генерации |
| `--tasks-path` | Путь к JSON-файлу задач (обязателен для `custom`) |
| `--output` | Файл для сохранения результатов (JSON) |

## Типичный сценарий

1. Прогоните baseline на базовой модели (`qwen2.5-coder:7b-instruct`)
2. Дообучите (см. [QLoRA](finetune.md))
3. Прогоните eval на `fluxion-coder-python`
4. Сравните pass@k

Результаты — основа для релизных заметок и таблицы качества в README.

## Baseline (qwen2.5-coder:7b-instruct)

| Бенчмарк | Задач | pass@1 | Дата |
|----------|-------|--------|------|
| HumanEval | 10 | 100.0% | 2026-08-06 |
| MBPP (sanitized) | 20 | 95.0% | 2026-08-20 |

Файлы результатов: `data/eval_humaneval_baseline.json`, `data/eval_mbpp_baseline.json`.

Датасет MBPP: `google-research-datasets/mbpp` (config `sanitized`, split `test`). Тесты (`test_list`) передаются модели в промпте — как в bigcode-evaluation-harness; исполняется только сгенерированный код.

## Custom-эвалы (проектные задачи)

`--benchmark custom` запускает eval на собственных задачах из JSON-файла —
регрессия качества именно на вашем коде/домене.

```bash
python -m eval.runner --benchmark custom --tasks-path eval/tasks.sample.json
```

Формат файла — массив задач (или объект с ключом `"tasks"`):

```json
{
  "tasks": [
    {
      "task_id": "fluxion-add-1",
      "prompt": "Write a function `add(a, b)` that returns the sum of two integers.",
      "canonical_solution": "def add(a, b):\n    return a + b\n",
      "test": "assert add(2, 3) == 5\nassert add(-1, 1) == 0\n",
      "entry_point": "add"
    }
  ]
}
```

Обязательные поля — `task_id` и `prompt`; опциональные: `canonical_solution`,
`test` (передаётся модели в промпте и исполняется вместе с решением),
`entry_point`, `imports` (строка или список строк — добавляется перед тестами).
Невалидный файл — `ValueError` с указанием задачи.

Как и MBPP, тесты идут в промпте, исполняется только сгенерированный код
(`eval/runner.py: run_code_safely`). Пример файла: `eval/tasks.sample.json`.
Harness-smoke (парсер + прогон канонических решений без модели) гоняется в CI
(`tests/test_eval_custom.py`).
