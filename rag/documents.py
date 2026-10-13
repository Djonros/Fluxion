"""Text of office documents for the project index: PDF, Word, Excel, PowerPoint.

Word, Excel and PowerPoint files are zip archives of XML and are read with the
standard library.  PDF needs ``pypdf``; without it PDF files are skipped.
"""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx"}

MAX_FILE_BYTES = 50 * 1024 * 1024  # larger documents are skipped
MAX_TEXT_CHARS = 2_000_000  # cap per document: a huge table must not flood the index
MAX_XML_BYTES = 64 * 1024 * 1024  # one unpacked part: guards against zip bombs

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class DocumentError(RuntimeError):
    """The document cannot be read (damaged, encrypted, too large)."""


def is_document(path: str | Path) -> bool:
    return Path(path).suffix.lower() in DOCUMENT_EXTENSIONS


def pdf_supported() -> bool:
    try:
        import pypdf  # noqa: F401
    except Exception:
        return False
    return True


def extract_text(path: str | Path) -> str:
    """Plain text of a document; raises DocumentError when it cannot be read."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DocumentError(str(exc)) from exc
    if size > MAX_FILE_BYTES:
        raise DocumentError(f"{path.name}: больше {MAX_FILE_BYTES // (1024 * 1024)} МБ, пропущен")
    try:
        if ext == ".pdf":
            text = _pdf_text(path)
        elif ext == ".docx":
            text = _docx_text(path)
        elif ext == ".xlsx":
            text = _xlsx_text(path)
        elif ext == ".pptx":
            text = _pptx_text(path)
        else:
            raise DocumentError(f"{path.name}: формат не поддерживается")
    except DocumentError:
        raise
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, ValueError, OSError) as exc:
        raise DocumentError(f"{path.name}: файл повреждён или защищён ({exc})") from exc
    return text[:MAX_TEXT_CHARS]


# ── PDF ─────────────────────────────────────────────────────────────────────

def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise DocumentError(f"{path.name}: для PDF нужен пакет pypdf") from exc
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise DocumentError(f"{path.name}: PDF защищён паролем")
        pages = []
        total = 0
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[Страница {number}]\n{text}")
                total += len(text)
            if total > MAX_TEXT_CHARS:
                break
    except DocumentError:
        raise
    except Exception as exc:  # pypdf raises many own error types
        raise DocumentError(f"{path.name}: PDF не читается ({exc})") from exc
    return "\n\n".join(pages)


# ── Office Open XML (zip of XML parts) ──────────────────────────────────────

def _read_part(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise DocumentError(f"часть {name} слишком велика")
    return ElementTree.fromstring(archive.read(info))


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = _read_part(archive, "word/document.xml")
    paragraphs = []
    for para in root.iter(f"{_W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _natural_key(name: str) -> list:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def _xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for item in _read_part(archive, "xl/sharedStrings.xml").iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{_S}t")))
        sheet_names = _xlsx_sheet_names(archive, names)
        sheets = sorted(
            (n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)), key=_natural_key
        )
        blocks = []
        total = 0
        for name in sheets:
            title = sheet_names.get(name) or Path(name).stem
            rows = []
            for row in _read_part(archive, name).iter(f"{_S}row"):
                cells = [_xlsx_cell(cell, shared) for cell in row.iter(f"{_S}c")]
                line = "\t".join(cells).strip()
                if line:
                    rows.append(line)
                    total += len(line)
                if total > MAX_TEXT_CHARS:
                    break
            if rows:
                blocks.append(f"[Лист {title}]\n" + "\n".join(rows))
            if total > MAX_TEXT_CHARS:
                break
    return "\n\n".join(blocks)


def _xlsx_sheet_names(archive: zipfile.ZipFile, names: list[str]) -> dict[str, str]:
    """worksheet part -> sheet title, from workbook.xml and its relationships."""
    if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
        return {}
    targets = {}
    for rel in _read_part(archive, "xl/_rels/workbook.xml.rels"):
        target = rel.get("Target", "").lstrip("/")
        targets[rel.get("Id")] = target if target.startswith("xl/") else f"xl/{target}"
    titles = {}
    for sheet in _read_part(archive, "xl/workbook.xml").iter(f"{_S}sheet"):
        part = targets.get(sheet.get(f"{_R}id"))
        if part:
            titles[part] = sheet.get("name", "")
    return titles


def _xlsx_cell(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.get("t", "")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_S}t")).strip()
    value = cell.find(f"{_S}v")
    text = value.text if value is not None and value.text else ""
    if kind == "s" and text.isdigit():
        index = int(text)
        return shared[index].strip() if index < len(shared) else ""
    if kind == "b":
        return "ИСТИНА" if text == "1" else "ЛОЖЬ"
    return text.strip()


def _pptx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        slides = sorted(
            (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=_natural_key,
        )
        blocks = []
        for number, name in enumerate(slides, start=1):
            root = _read_part(archive, name)
            lines = []
            for para in root.iter(f"{_A}p"):
                text = "".join(t.text or "" for t in para.iter(f"{_A}t")).strip()
                if text:
                    lines.append(text)
            if lines:
                blocks.append(f"[Слайд {number}]\n" + "\n".join(lines))
    return "\n\n".join(blocks)
