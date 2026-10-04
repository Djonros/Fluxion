# Fluxion Training Package (portable)

Self-contained QLoRA fine-tuning package for `Qwen/Qwen2.5-Coder-7B-Instruct`.
Move this folder to the GPU machine and follow the steps below.

## Requirements

- NVIDIA GPU: 6–12 GB VRAM
- Driver with CUDA 12.1+ support
- Python 3.11 or 3.12

## Setup

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-train.txt
```

Linux: `python3.11 -m venv .venv && source .venv/bin/activate`.

## 1. Build dataset

```powershell
python -m data_pipeline.run codesearchnet --limit 5000 --output data/instruction_train.jsonl
```

Alternative instruction datasets:

```powershell
python -m data_pipeline.run instruction --dataset code_alpaca --output data/instruction_train.jsonl
```

Output is ChatML-formatted JSONL. The `clone` subcommand needs the full
Fluxion repo (`rag/`, `core/`) and is not part of this package.

## 2. Train (QLoRA)

6 GB VRAM (unsloth, recommended):

```powershell
python -m finetune.train_unsloth --preset low --dataset data/instruction_train.jsonl
```

8–12 GB VRAM (HF peft + trl):

```powershell
python -m finetune.train_hf --preset standard --dataset data/instruction_train.jsonl
```

Optional flags: `--max-samples N`, `--epochs N`. Adapter is written to
`data/lora_output`.

## 3. Merge adapter into base model

```powershell
python -m finetune.merge --adapter data/lora_output --output data/merged_model
```

## 4. Export GGUF (optional)

Requires a llama.cpp checkout (built with cmake):

```powershell
python -m finetune.export_gguf --model data/merged_model --llama-cpp-dir C:\path\to\llama.cpp --quantize q4_K_M
```

Or set `LLAMA_CPP_DIR` instead of `--llama-cpp-dir`. Output: `data/gguf`.

## 5. Use the model

**In Fluxion:** page «Модели» → «Импорт с диска…» → the `.gguf` file from
`data/gguf` (or copy it to `%LOCALAPPDATA%\Fluxion\models`), then pick it in the
chat's «Модель» list. Training from the app's «Обучение» page does this
automatically when `LLAMA_CPP_DIR` is set.

**With Ollama (optional):** create a `Modelfile` with
`FROM ./data/gguf/model-q4_K_M.gguf`, then `ollama create fluxion-coder -f Modelfile`.

## Contents

| Path | Purpose |
| --- | --- |
| `finetune/qlora_config.py` | QLoRASettings, LOW/STANDARD VRAM presets |
| `finetune/train_unsloth.py` | 6 GB training path (unsloth) |
| `finetune/train_hf.py` | 8–12 GB training path (peft + trl) |
| `finetune/dataset_loader.py` | ChatML formatting, sequence packing |
| `finetune/merge.py` | Adapter → full weights merge |
| `finetune/export_gguf.py` | GGUF export via llama.cpp |
| `data_pipeline/` | fetch → filter → dedup → JSONL |
| `requirements-train.txt` | Training dependencies |
