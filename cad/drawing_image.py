"""Чертёж из PDF или картинки → текстовая сводка для диалога «/компас».

Скан или экспорт чертежа (PDF, PNG, JPEG, TIFF, BMP) переводится в тот же
текст, что DXF/DWG (cad/drawing_input.py): модель читает размеры, надписи и
материал словами. PDF-страницы с текстом читает pypdf (rag/documents.py),
страницы-сканы и картинки — распознавание Windows (rag/ocr.py), так же, как
их читает «Архив КД». Размер картинки в пикселях тоже попадает в сводку.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from rag.ocr import IMAGE_EXTENSIONS

from .drawing_input import DrawingInputError, TEXT_LENGTH, TEXT_LIMIT

SUFFIXES = {".pdf", *IMAGE_EXTENSIONS}

PAGE_MARK = re.compile(r"\[Страница (\d+)\]\n?")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _pages(text: str) -> list[tuple[int, str]]:
    """[(page, text)] of «[Страница N]\n…» blocks, the way rag/documents.py writes them."""
    marks = list(PAGE_MARK.finditer(text))
    out = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[mark.end():end].strip()
        if body:
            out.append((int(mark.group(1)), body))
    return out


def _texts(lines: list[str]) -> str:
    """«Надписи: …» — уникальные строки; голое число (только размер) не надпись."""
    clean: list[str] = []
    for line in lines:
        line = " ".join(line.split())
        if not line or _NUMBER.fullmatch(line):
            continue
        if len(line) > TEXT_LENGTH:
            line = line[:TEXT_LENGTH]
        if line not in clean:
            clean.append(line)
        if len(clean) >= TEXT_LIMIT:
            break
    return "Надписи: " + ", ".join(f"«{t}»" for t in clean) + "." if clean else ""


def _sizes(lines: list[str]) -> str:
    """«Числа на чертеже: …» — все числа (размеры) в порядке чтения, не больше 40."""
    numbers: list[str] = []
    for line in lines:
        for match in _NUMBER.finditer(line):
            value = match.group().replace(",", ".")
            if value not in numbers:
                numbers.append(value)
        if len(numbers) >= 40:
            break
    return "Числа на чертеже: " + ", ".join(numbers) + "." if numbers else ""


def _image_size(path: Path) -> tuple[int, int]:
    """Pixels of the picture (Pillow), for the scale note of the summary."""
    from PIL import Image

    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError) as exc:
        raise DrawingInputError(f"картинка не открывается ({exc})") from exc


def _image_summary(path: Path, describe=None) -> str:
    """Сводка картинки-чертежа: пиксели + распознанный текст (Windows OCR).

    *describe* — чтение картинки vision-моделью (desktop_browser/attachments.py):
    когда модель для фото на ПК есть, её описание идёт в сводку вместе с OCR."""
    from rag.ocr import available, text_of_image

    if not available():
        if describe is None:
            raise DrawingInputError(
                "в Windows нет распознавания текста для русского языка: "
                "Параметры → Время и язык → Язык → добавьте русский языковой пакет.")
        text = ""
    else:
        text = (text_of_image(path) or "").strip()
        if not text and describe is None:
            raise DrawingInputError("в картинке не распознан текст: фото размытое или это не чертёж.")
    width, height = _image_size(path)
    lines = [f"[Чертёж {path.name}]",
             f"Картинка: {width} × {height} пикселей."]
    found = _texts(text.splitlines())
    if found:
        lines.append(found)
    sizes = _sizes(text.splitlines())
    if sizes:
        lines.append(sizes)
    if describe is not None:
        seen = describe(path)
        if seen:
            lines.append("Описание модели для фото:\n" + seen.strip())
    if len(lines) < 3:
        raise DrawingInputError("в картинке не найдено ни текста, ни описания модели для фото.")
    return "\n".join(lines)


def _pdf_summary(path: Path) -> str:
    """Сводка PDF-чертежа: текст страниц (pypdf), скан-страницы — OCR."""
    from rag import documents, ocr

    try:
        text = documents.extract_text(path)
    except Exception as exc:
        raise DrawingInputError(str(exc) or "PDF не читается.") from exc
    if not ocr.available():
        # без OCR читается только векторный PDF (текст страниц); скан — нет
        pages = _pages(text)
        if not pages:
            raise DrawingInputError(
                "в PDF нет текста, а распознавание Windows недоступно: "
                "Параметры → Время и язык → Язык → добавьте русский языковой пакет.")
        return _from_pages(path.name, pages)
    folder = Path(tempfile.mkdtemp(prefix="fluxion-drawing-"))
    try:
        scans = ocr.pdf_scan_pages(path, folder)
        recognised = ocr.recognize(list(scans.values())) if scans else {}
    except ocr.OcrError as exc:
        raise DrawingInputError(str(exc)) from exc
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    merged: dict[int, str] = {}
    for number, body in _pages(text):
        merged[number] = body
    for number, picture in scans.items():
        found = (recognised.get(str(picture), "") or "").strip()
        if found:
            merged[number] = f"{merged.get(number, '')}\n{found}".strip()
    if not merged:
        raise DrawingInputError("в PDF не найдено ни текста, ни картинок с надписями.")
    return _from_pages(path.name, sorted(merged.items()))


def _from_pages(name: str, pages: list[tuple[int, str]]) -> str:
    """Сводка из текстов страниц: надписи и числа каждой страницы."""
    lines = [f"[Чертёж {name}]"]
    for number, body in pages:
        lines.append(f"[Страница {number}]")
        found = _texts(body.splitlines())
        if found:
            lines.append(found)
        sizes = _sizes(body.splitlines())
        if sizes:
            lines.append(sizes)
    return "\n".join(lines)


def summarize(path: str | Path, log=print, describe=None) -> str:
    """Сводка чертежа из PDF или картинки — текстовый блок для модели.

    Для картинки *describe(path)* — чтение vision-моделью, если она есть."""
    path = Path(path)
    if not path.is_file():
        raise DrawingInputError("файл не найден.")
    if path.suffix.lower() == ".pdf":
        return _pdf_summary(path)
    return _image_summary(path, describe)


def blocks(paths, log=print, describe=None) -> tuple[str, list[str]]:
    """Сводки нескольких чертежей: текст к описанию и проблемы (не прочитано)."""
    parts: list[str] = []
    problems: list[str] = []
    for path in paths:
        try:  # один битый файл не должен отменять сводки остальных
            parts.append(summarize(path, log=log, describe=describe))
        except Exception as exc:
            problems.append(f"{Path(path).name}: {exc}")
    return "\n\n".join(parts), problems
