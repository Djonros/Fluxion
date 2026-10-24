"""Training dataset from the user's own documents.

The chat model reads the documents fragment by fragment and writes
question-answer pairs on each one; the user looks them through, corrects or
drops some and saves the rest as a ChatML JSONL the trainer reads
(``finetune.dataset_loader``). Only the standard library and ``rag.documents``
are used, so the slim desktop build can run it without the training stack.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

TEXT_EXTENSIONS = {".txt", ".md"}
FRAGMENT_CHARS = 3000  # one model call: fits a small context with room for the answer
MIN_FRAGMENT_CHARS = 200  # a title page or a table of contents gives no useful questions
MIN_ANSWER_CHARS = 20
DEFAULT_PER_FRAGMENT = 3
SYSTEM_PROMPT = (
    "Ты — помощник инженера-конструктора. Отвечай точно и по делу, "
    "опираясь на нормы и документы предприятия."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["question", "answer"],
            },
        }
    },
    "required": ["pairs"],
}


class DocDatasetError(RuntimeError):
    """Nothing to build the dataset from (no readable documents, the model fails)."""


@dataclass
class QAPair:
    question: str
    answer: str
    source: str = ""  # file name and fragment number, shown in the review window
    keep: bool = True


def supported(path: str | Path) -> bool:
    from rag.documents import DOCUMENT_EXTENSIONS

    return Path(path).suffix.lower() in DOCUMENT_EXTENSIONS | TEXT_EXTENSIONS


def collect_files(paths: Iterable[str | Path]) -> list[Path]:
    """Documents among *paths*; a folder is searched with its subfolders."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*") if p.is_file() and supported(p)))
        elif path.is_file() and supported(path):
            found.append(path)
    return list(dict.fromkeys(found))


def read_text(path: Path) -> str:
    """Plain text of a document; raises rag.documents.DocumentError."""
    if path.suffix.lower() in TEXT_EXTENSIONS:
        from rag.documents import DocumentError

        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise DocumentError(f"{path.name}: {exc}") from exc
    from rag.documents import extract_text

    return extract_text(path)


def split_fragments(text: str, max_chars: int = FRAGMENT_CHARS) -> list[str]:
    """Paragraph-aligned pieces of at most *max_chars* (a longer paragraph is cut)."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    fragments: list[str] = []
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > max_chars:
            if current:
                fragments.append(current)
                current = ""
            cut = paragraph.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            fragments.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + 2 + len(paragraph) > max_chars:
            fragments.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        fragments.append(current)
    return [f for f in fragments if len(f) >= MIN_FRAGMENT_CHARS]


def prompt(fragment: str, count: int) -> list[dict[str, str]]:
    system = (
        "Ты готовишь данные для дообучения языковой модели. По фрагменту документа "
        f"составь {count} пары «вопрос — ответ» на русском языке. Вопросы такие, какие "
        "задал бы инженер в работе. Ответ полный и самодостаточный, только по тексту "
        "фрагмента, без слов «в тексте сказано» и без ссылок на фрагмент. Если во "
        "фрагменте нет полезных сведений (оглавление, титульный лист), верни пустой "
        'список. Ответ — JSON: {"pairs": [{"question": "...", "answer": "..."}]}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": fragment},
    ]


def _extract_json(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def parse_pairs(raw: str, source: str = "") -> list[QAPair]:
    """Pairs from the model's reply; malformed or too short items are dropped."""
    data = _extract_json(raw or "")
    items = data.get("pairs") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    pairs = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or item.get("вопрос") or "").strip()
        answer = str(item.get("answer") or item.get("ответ") or "").strip()
        if len(question) >= 8 and len(answer) >= MIN_ANSWER_CHARS:
            pairs.append(QAPair(question, answer, source))
    return pairs


def ask_pairs(fragment: str, backend, count: int, source: str = "") -> list[QAPair]:
    """One model call: a fragment -> its question-answer pairs."""
    from core.config import GenerationSettings

    gen = GenerationSettings(temperature=0.3, max_tokens=400 * count + 200, json_schema=SCHEMA)
    messages = prompt(fragment, count)
    try:
        raw = backend.generate(messages, gen)
    except TypeError:
        raw = backend.generate(messages)
    return parse_pairs(raw, source)[:count]


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def dedupe(pairs: list[QAPair]) -> list[QAPair]:
    """Drops pairs whose question repeats an earlier one (case and punctuation ignored)."""
    seen: set[str] = set()
    out = []
    for pair in pairs:
        key = _key(pair.question)
        if key and key not in seen:
            seen.add(key)
            out.append(pair)
    return out


def build_pairs(
    files: list[Path],
    backend,
    *,
    per_fragment: int = DEFAULT_PER_FRAGMENT,
    max_fragments: int = 0,
    log: Callable[[str], None] = lambda line: None,
    progress: Callable[[int, int], None] = lambda done, total: None,
    stop_requested: Callable[[], bool] = lambda: False,
) -> list[QAPair]:
    """Pairs from every readable document; unreadable files and failed calls are logged."""
    from rag.documents import DocumentError

    work: list[tuple[str, str]] = []  # (source label, fragment)
    for path in files:
        try:
            text = read_text(path)
        except DocumentError as exc:
            log(f"Пропущен: {exc}")
            continue
        fragments = split_fragments(text)
        if not fragments:
            log(f"Пропущен: {path.name} — нет текста (скан без распознавания?)")
            continue
        log(f"{path.name}: фрагментов {len(fragments)}")
        work.extend((f"{path.name}, фрагмент {i}", f) for i, f in enumerate(fragments, 1))
    if not work:
        raise DocDatasetError("В выбранных файлах нет текста, из которого можно составить вопросы.")
    if max_fragments and len(work) > max_fragments:
        log(f"Взяты первые {max_fragments} фрагментов из {len(work)}.")
        work = work[:max_fragments]

    pairs: list[QAPair] = []
    failures = 0
    progress(0, len(work))
    for done, (source, fragment) in enumerate(work, 1):
        if stop_requested():
            log("Остановлено: сохранятся пары, готовые к этому моменту.")
            break
        try:
            pairs.extend(ask_pairs(fragment, backend, per_fragment, source))
        except Exception as exc:  # one bad call must not lose the rest
            failures += 1
            log(f"{source}: модель не ответила ({exc})")
            if failures >= 3 and not pairs:
                raise DocDatasetError(f"Модель не отвечает: {exc}") from exc
        progress(done, len(work))
    return dedupe(pairs)


def to_record(pair: QAPair, system: str = SYSTEM_PROMPT) -> dict:
    messages = [{"role": "system", "content": system}] if system else []
    messages += [
        {"role": "user", "content": pair.question},
        {"role": "assistant", "content": pair.answer},
    ]
    return {"messages": messages, "source": pair.source}


def save_jsonl(pairs: list[QAPair], path: str | Path, system: str = SYSTEM_PROMPT) -> int:
    """Writes the kept pairs as ChatML JSONL; returns their number."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = [p for p in pairs if p.keep and p.question.strip() and p.answer.strip()]
    with path.open("w", encoding="utf-8") as fh:
        for pair in kept:
            fh.write(json.dumps(to_record(pair, system), ensure_ascii=False) + "\n")
    return len(kept)


def save_draft(pairs: list[QAPair], path: str | Path) -> None:
    """All pairs with their marks, so an interrupted review can be continued."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps([asdict(p) for p in pairs], ensure_ascii=False, indent=1), encoding="utf-8"
    )


def load_draft(path: str | Path) -> list[QAPair]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [QAPair(**{k: item[k] for k in ("question", "answer", "source", "keep") if k in item})
            for item in data if isinstance(item, dict)]
