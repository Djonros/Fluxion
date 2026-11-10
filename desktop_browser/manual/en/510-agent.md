# Agent
<!-- раздел: Агент и обучение | инструмент: - -->
## What it is for

To solve a task in code step by step: find the files, read the code, run the tests, fix the error.

## How to start

**Ctrl+Enter** in the chat, or the **+** button at the input field → «Agent: files» and «Plan».

## Step by step

1. Describe the task: «The test tests/test_cart.py fails, find and fix the error».
2. Without a licence the agent only reads and explains (plus three trial edits).
3. «Plan» — the agent makes a plan, and «▶ Run the plan» appears under the answer.

## What you get

The steps of the agent and the result are seen in the chat. Before the first edit it takes a snapshot of the project — everything can be rolled back.

## If it does not work

- **Checking with tests** — the chat settings button (the gear) → «Check changes with tests»: «Auto» (the default: tests run only when the project has tests of its own, else the syntax is checked quietly), «Always» or «Never». Before the first change the agent notes which tests failed already and answers only for new failures; when two runs in a row give the same result, it stops and says what does not pass and what it changed.
- Tests do not run — install pytest in the venv of the project or set the Python in `FLUXION_TEST_PYTHON`.
- The step limit — the chat settings button (the gear) → «Max iterations»; in Pro — «No limit».
