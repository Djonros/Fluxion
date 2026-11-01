# Dataset from documents
<!-- раздел: Агент и обучение | инструмент: dataset -->
## What it is for

To make a «question — answer» dataset for fine-tuning from your documents: standards, company standards, instructions.

## How to start

The «Training» page → the **«From documents…»** button.

## Step by step

1. Add files or a folder: PDF, Word, Excel, PowerPoint, txt, md.
2. Choose how many questions to make per fragment (about 3000 characters).
3. **«Make questions»**. For a trial set «Fragments at most» to 3–5.
4. Look through the table: an answer is corrected right in it; untick a bad pair.
5. **«Save dataset…»** — the .jsonl file is put into the dataset field by itself.

## What you get

A .jsonl file for fine-tuning. If you close the window, you continue from the same place the next time.

## If it does not work

- Slow on a processor: a 7B model takes 2–5 minutes per fragment; with a cloud model (Pro) — seconds.
- The dataset can be made on any computer and trained where there is an NVIDIA graphics card.
