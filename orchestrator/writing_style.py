"""«Мой стиль»: the chat writes memos, explanatory notes and letters to customers
the way the user does.

The user gives a few of their own texts; the chat model describes the manner
of writing once (``analyze``) and the description — the profile — is kept with
the samples in ``writing_style.json``. While «Мой стиль» is on, every chat
request gets the profile and one or two samples of the same kind of text
(``prompt_block``). No training and no graphics card: a feature of Fluxion Pro.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

PRO_FEATURE = "writing_style"
FILE_NAME = "writing_style.json"

MEMO, NOTE, LETTER, OTHER = "Служебная записка", "Пояснительная записка", "Письмо заказчику", "Другое"
KINDS = (MEMO, NOTE, LETTER, OTHER)
# what a request asks to write ("напиши служебку", "поясниловку к проекту", "ответ заказчику")
KIND_PATTERNS = (
    (MEMO, re.compile(r"служебк|служебн\w*\s+записк|докладн", re.IGNORECASE)),
    (NOTE, re.compile(r"пояснител|пояснилов|\bпз\b", re.IGNORECASE)),
    (LETTER, re.compile(r"письм|заказчик|клиент|ответ\w*\s+на\s+(?:запрос|претензи)|претензи", re.IGNORECASE)),
)

SAMPLE_CHARS = 6000  # of one sample for the analysis: a long report is cut, its manner is in the first pages
PROMPT_SAMPLE_CHARS = 1500  # of one sample shown to the chat with a request
PROMPT_SAMPLES = 2
MIN_SAMPLE_CHARS = 80


class WritingStyleError(RuntimeError):
    """No samples to learn from, or the model failed."""


@dataclass
class Sample:
    name: str  # file name or "Вставленный текст"
    kind: str
    text: str


@dataclass
class StyleProfile:
    profile: str = ""  # the manner of writing as the model described it (the user may edit it)
    samples: list[Sample] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.profile.strip())


def style_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / FILE_NAME


def load(data_dir: str | Path) -> StyleProfile:
    try:
        data = json.loads(style_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return StyleProfile()
    if not isinstance(data, dict):
        return StyleProfile()
    samples = []
    for item in data.get("samples") or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            kind = item.get("kind") if item.get("kind") in KINDS else OTHER
            samples.append(Sample(str(item.get("name") or ""), kind, str(item["text"])))
    return StyleProfile(str(data.get("profile") or ""), samples)


def save(data_dir: str | Path, style: StyleProfile) -> Path:
    path = style_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"profile": style.profile, "samples": [asdict(s) for s in style.samples]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def detect_kind(text: str) -> str:
    """The kind of text a request or a sample is (OTHER when it does not say)."""
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(text or ""):
            return kind
    return OTHER


def read_sample(path: str | Path) -> Sample:
    """A sample from a document (PDF, Word, text…); its kind guessed by its name and beginning."""
    from finetune.doc_dataset import read_text

    path = Path(path)
    text = read_text(path).strip()
    return Sample(path.name, detect_kind(f"{path.stem}\n{text[:400]}"), text)


def analysis_prompt(samples: list[Sample]) -> list[dict[str, str]]:
    system = (
        "Ты — редактор. По образцам текстов одного автора опиши его манеру письма так, чтобы "
        "другой человек мог писать неотличимо. Пиши по-русски, коротким списком, по пунктам:\n"
        "- обращение, приветствие, подпись и заключительная фраза (дословно, если они повторяются);\n"
        "- официальность и тон, от какого лица пишет (я / мы / безлично);\n"
        "- длина фраз и абзацев, как построен текст (шапка, вступление, суть, просьба, итог);\n"
        "- любимые обороты и термины (дословно), каких слов избегает;\n"
        "- как оформлены списки, номера, даты, ссылки на документы и приложения, единицы измерения;\n"
        "- отличия для каждого вида текста (служебная записка, пояснительная записка, письмо заказчику), "
        "если образцы есть.\n"
        "Не пересказывай содержание образцов, не придумывай того, чего в них нет. Без вступления."
    )
    body = "\n\n".join(
        f"=== Образец {i} — {s.kind} ({s.name}) ===\n{s.text[:SAMPLE_CHARS]}" for i, s in enumerate(samples, 1)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": body}]


def analyze(samples: list[Sample], backend) -> str:
    """One model call: the samples -> the profile."""
    usable = [s for s in samples if len(s.text.strip()) >= MIN_SAMPLE_CHARS]
    if not usable:
        raise WritingStyleError(f"Нужен хотя бы один образец длиннее {MIN_SAMPLE_CHARS} знаков.")
    from core.config import GenerationSettings

    messages = analysis_prompt(usable)
    gen = GenerationSettings(temperature=0.2, max_tokens=900)
    try:
        raw = backend.generate(messages, gen)
    except TypeError:
        raw = backend.generate(messages)
    except Exception as exc:
        raise WritingStyleError(f"Модель не ответила: {exc}") from exc
    profile = (raw or "").strip()
    if not profile:
        raise WritingStyleError("Модель вернула пустой ответ — попробуйте ещё раз или другую модель.")
    return profile


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w{4,}", text.lower())}


def pick_samples(style: StyleProfile, query: str, limit: int = PROMPT_SAMPLES) -> list[Sample]:
    """The samples closest to the request: its kind first, then the most words in common."""
    kind = detect_kind(query)
    words = _words(query)
    ranked = sorted(style.samples, key=lambda s: (kind == OTHER or s.kind != kind, -len(words & _words(s.text))))
    return ranked[:limit]


def prompt_block(style: StyleProfile, query: str) -> str:
    """What the chat gets with a request while «Мой стиль» is on ("" without a profile)."""
    if not style.ready:
        return ""
    kind = detect_kind(query)
    lines = [
        "Пиши в манере пользователя: служебные и пояснительные записки, письма заказчикам и другие "
        "тексты от его имени — так, как пишет он сам. Его манера:",
        style.profile.strip(),
    ]
    if kind != OTHER:
        lines.append(f"Сейчас нужен вид текста: {kind}.")
    samples = pick_samples(style, query)
    if samples:
        lines.append("Образцы его текстов — только для манеры, не для содержания:")
        for s in samples:
            text = s.text.strip()
            if len(text) > PROMPT_SAMPLE_CHARS:
                text = text[:PROMPT_SAMPLE_CHARS].rsplit(" ", 1)[0] + " …"
            lines.append(f"--- {s.kind} ---\n{text}")
    lines.append("Факты, даты, номера и суммы бери только из запроса; чего не хватает — оставь [в скобках].")
    return "\n\n".join(lines)
