"""Files attached to a chat or agent message: «+» → «Прикрепить файл…», drag and drop, Ctrl+V.

A document becomes text in front of the question: PDF, Word, Excel, PowerPoint, the
KOMPAS-3D files, DXF/DWG and old .doc/.xls are read the way the design archive reads
them (rag/archive.py), any other file as plain text (code, CSV, logs). A picture is
described by the vision model of the PC — the one «Связь с телефоном» uses
(phone/vision.py) — and the description goes in front of the question the same way.
"""
from __future__ import annotations

import base64
import mimetypes
import threading
from pathlib import Path

from .i18n import tr

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
MAX_ATTACHMENTS = 8
MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024  # a plain-text file larger than this is not text to read
TEXT_LIMIT = 40_000  # characters of one file in front of the question: the model's context is small
IMAGE_PROMPT = ("Опиши изображение подробно и точно: что на нём, текст и размеры, если видны, "
                "повреждения и особенности. Пользователь спрашивает: {question}")

_vision_lock = threading.Lock()
_vision_engine = None  # kept between messages: the embedded model loads for a while


class AttachmentError(RuntimeError):
    """A file that cannot be attached; the text is shown to the user."""


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def human_size(size: int) -> str:
    if size < 1024 * 1024:
        return tr("{value} КБ", value=max(1, round(size / 1024)))
    return tr("{value} МБ", value=f"{size / (1024 * 1024):.1f}".replace(".", ","))


def describe(path: str | Path) -> dict:
    """The entry the window keeps for an attached file; raises AttachmentError."""
    path = Path(path)
    if not path.is_file():
        raise AttachmentError(tr("{name}: файл не найден", name=path.name))
    return {"path": str(path), "name": path.name, "size": path.stat().st_size, "image": is_image(path)}


def read_text(path: str | Path) -> str:
    """The text of a document or a plain-text file, cut to TEXT_LIMIT; raises AttachmentError."""
    from rag.archive import ARCHIVE_EXTENSIONS, read_file

    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in ARCHIVE_EXTENSIONS:
        try:
            text, _year = read_file(path)
        except Exception as exc:  # DocumentError, KompasFileError, LegacyFileError and broken files
            raise AttachmentError(str(exc) or tr("{name}: не удалось прочитать", name=path.name)) from exc
    else:
        if path.stat().st_size > MAX_TEXT_FILE_BYTES:
            raise AttachmentError(tr("{name}: больше 5 МБ — это не текстовый файл", name=path.name))
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            raise AttachmentError(tr("{name}: формат не поддерживается", name=path.name))
        for encoding in ("utf-8-sig", "cp1251"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode("utf-8", errors="replace")
    text = text.strip()
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT] + "\n" + tr("[… файл обрезан: показаны первые {value} символов]", value=TEXT_LIMIT)
    return text


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def describe_image(path: str | Path, question: str, ollama_host: str) -> str:
    """What the vision model sees in the picture; raises AttachmentError without a vision model."""
    global _vision_engine
    from phone import config, vision

    path = Path(path)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": IMAGE_PROMPT.format(question=question or "—")},
        {"type": "image_url", "image_url": {"url": _data_url(path)}},
    ]}]
    with _vision_lock:
        try:
            _vision_engine = vision.pick(config.load(), ollama_host, embedded=_vision_engine)
        except vision.VisionUnavailable as exc:
            raise AttachmentError(tr("Чтобы разобрать фото, нужна модель для фото: Связь → «Связь с телефоном…» "
                                     "→ «Скачать модель для фото» (около {value} ГБ).",
                                     value=str(vision.DOWNLOAD_GB).replace(".", ","))) from exc
        try:
            return _vision_engine.complete(messages, temperature=0.2, max_tokens=700).strip()
        except Exception as exc:
            if vision.out_of_memory(exc):
                raise AttachmentError(tr("Не хватило памяти для модели для фото: закройте другие программы "
                                         "или используйте компьютер мощнее.")) from exc
            raise AttachmentError(tr("{name}: модель для фото не ответила ({exc})", name=path.name, exc=exc)) from exc


def unload_idle_vision() -> None:
    """Free the video memory of the embedded vision model after IDLE_UNLOAD without pictures.
    Called from the window's timer: never waits while a picture is being described."""
    if _vision_engine is None or not _vision_lock.acquire(blocking=False):
        return
    try:
        _vision_engine.unload_if_idle()
    except Exception:
        pass
    finally:
        _vision_lock.release()


def context(files: list[dict], question: str, ollama_host: str) -> tuple[str, list[str]]:
    """(the block put in front of the question, the problems to show) for the attached *files*."""
    blocks: list[str] = []
    problems: list[str] = []
    for item in files:
        try:
            if item.get("image"):
                body = describe_image(item["path"], question, ollama_host)
                blocks.append(tr("Изображение «{name}» (описание модели для фото):", name=item["name"]) + "\n" + body)
            else:
                body = read_text(item["path"])
                if not body:
                    problems.append(tr("{name}: текста в файле нет", name=item["name"]))
                    continue
                blocks.append(tr("Файл «{name}»:", name=item["name"]) + "\n```\n" + body + "\n```")
        except AttachmentError as exc:
            problems.append(str(exc))
        except OSError as exc:
            problems.append(tr("{name}: не удалось прочитать ({exc})", name=item["name"], exc=exc))
    if not blocks:
        return "", problems
    return tr("Вложения к сообщению:") + "\n\n" + "\n\n".join(blocks), problems
