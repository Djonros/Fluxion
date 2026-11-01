# Model fine-tuning
<!-- раздел: Агент и обучение | инструмент: training | pro -->
## What it is for

To adapt the model to your examples (QLoRA) so that it writes in the style of your team.

## How to start

Menu **Models → «Model training…»** — the «Training» page. An NVIDIA graphics card with 6 GB or more is needed.

## Step by step

1. The program installs the training environment by itself (internet needed, several GB).
2. The dataset — a .jsonl file: «Choose…» or «From documents…».
3. The preset — «low» (6 GB) or «standard» (8–12 GB), or «Load from file…».
4. **«Start training»**.

## What you get

The ready model appears in the «Model» list of the chat under the given name.

## If it does not work

- The log is silent for more than 30 minutes — press «Stop».
- To build a ready model (GGUF) the llama.cpp folder in `LLAMA_CPP_DIR` is needed; otherwise a LoRA adapter is saved.
