"""Text of the other files of a design archive: AutoCAD drawings (DXF, DWG) and the old
binary Word and Excel files (.doc, .xls).

- DXF: the texts of the drawing with ezdxf — single and multiline texts, block
  attributes (the title block), dimension texts written by hand; the year it was
  made from the header.
- DWG: turned into DXF by the free ODA File Converter when it is installed
  (cad/acad.py finds it); AutoCAD is not started for a whole archive.
- .doc, .xls: the text runs of the file (Word and Excel keep Russian text as
  UTF-16, the rest as one byte per letter) — enough to find a document by its
  words, with no extra package.
Files are only read.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

DRAWING_EXTENSIONS = {".dxf", ".dwg"}
OLD_OFFICE_EXTENSIONS = {".doc", ".xls"}
LEGACY_EXTENSIONS = DRAWING_EXTENSIONS | OLD_OFFICE_EXTENSIONS
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
_CONTROL_RE = re.compile(r"\\[A-Za-z][^;\\]*;|\\[PpNnLlOoKk~]|[{}]")  # MTEXT formatting codes
_RUN_UTF16_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё№.,:;()«»\"'%/+\-–— ]{4,}")
_RUN_BYTES_RE = re.compile(rb"[\x20-\x7e\xa8\xb8\xb9\xc0-\xff]{5,}")
# names Word and Excel keep in every file: built-in cell styles, parts of the file
_NOISE_RE = re.compile(
    r"^(Calibri.*|Cambria.*|Arial.*|Times New Roman.*|Акцент\d|Заголовок \d|Ввод|Вывод|Вычисление|Итог|"
    r"Контрольная ячейка|Название|Нейтральный|Связанная ячейка|Текст предупреждения|Хороший|Плохой|"
    r"Пояснение|Примечание|Процентный|Финансовый|Денежный|Обычный|Настройки по умолчанию|Лист\d*|"
    r"Root Entry|Workbook|WordDocument|User Names|Revision Log|SummaryInformation|"
    r"DocumentSummaryInformation|CompObj|Normal\.dotm?|Microsoft (Office )?(Word|Excel).*|"
    r"TableStyle.*|.* - Личное представление)$", re.IGNORECASE)


class LegacyFileError(RuntimeError):
    """A drawing or an old document that cannot be read here."""


def _check_size(path: Path) -> None:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise LegacyFileError(f"{path.name}: больше {MAX_FILE_BYTES // (1024 * 1024)} МБ, пропущен")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _CONTROL_RE.sub(" ", text or "")).strip()


def _dxf_year(doc) -> int:
    """The year from $TDCREATE, a Julian day ("2461340.93" — 26.10.2026)."""
    from datetime import date

    try:
        return date.fromordinal(int(float(doc.header.get("$TDCREATE", 0)) - 1721424.5)).year
    except (TypeError, ValueError, OverflowError):
        return 0


def dxf_text(path: str | Path, title: str = "Чертёж DXF") -> tuple[str, int]:
    """(text, year) of a DXF: its texts, each once, in the order of the drawing."""
    path = Path(path)
    _check_size(path)
    try:
        from ezdxf import recover
    except ImportError as exc:
        raise LegacyFileError(f"{path.name}: для DXF нужен пакет ezdxf") from exc
    try:
        doc, _auditor = recover.readfile(str(path))
    except Exception as exc:  # ezdxf raises its own errors for broken files
        raise LegacyFileError(f"{path.name}: DXF не читается ({exc})") from exc
    texts: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        text = _clean(raw)
        if len(text) >= 2 and text not in seen and not re.fullmatch(r"[\d.,\s]+", text):
            seen.add(text)
            texts.append(text)

    spaces = [doc.modelspace(), *[layout for layout in doc.layouts if layout.name != "Model"], *doc.blocks]
    for space in spaces:
        for entity in space:
            kind = entity.dxftype()
            try:
                if kind in ("TEXT", "ATTRIB", "ATTDEF"):
                    add(entity.dxf.text)
                elif kind == "MTEXT":
                    add(entity.plain_text())
                elif kind == "DIMENSION" and entity.dxf.get("text", "") not in ("", "<>"):
                    add(entity.dxf.text.replace("<>", ""))
                elif kind == "INSERT":
                    for attrib in entity.attribs:
                        add(attrib.dxf.text)
            except Exception:
                continue
        if sum(map(len, texts)) > MAX_TEXT_CHARS:
            break
    return f"{title}: {path.stem}\n" + "\n".join(texts), _dxf_year(doc)


def dwg_text(path: str | Path) -> tuple[str, int]:
    """(text, year) of a DWG through the ODA File Converter; raises LegacyFileError without it."""
    path = Path(path)
    _check_size(path)
    from cad.acad import dwg_to_dxf_oda, oda_converter

    exe = oda_converter()
    if not exe:
        raise LegacyFileError(f"{path.name}: для DWG нужен бесплатный ODA File Converter")
    with tempfile.TemporaryDirectory(prefix="fluxion-dwg-") as folder:
        try:
            dxf = dwg_to_dxf_oda(path, Path(folder), exe, log=lambda line: None)
        except Exception as exc:
            raise LegacyFileError(f"{path.name}: DWG не перевёлся в DXF ({exc})") from exc
        return dxf_text(dxf, title="Чертёж DWG")


def old_office_text(path: str | Path) -> str:
    """The text runs of an old .doc or .xls: UTF-16 runs (Russian text) and one-byte runs."""
    path = Path(path)
    _check_size(path)
    data = path.read_bytes()
    if data[:4] != b"\xd0\xcf\x11\xe0":
        raise LegacyFileError(f"{path.name}: не документ Word/Excel старого формата")
    runs: list[str] = []
    seen: set[str] = set()
    for offset in (0, 1):  # UTF-16 text may start at an odd byte
        for match in _RUN_UTF16_RE.finditer(data[offset:].decode("utf-16-le", "ignore")):
            runs.append(match.group(0))
    for match in _RUN_BYTES_RE.finditer(data):
        runs.append(match.group(0).decode("cp1251", "ignore"))
    out = []
    for run in runs:
        text = " ".join(run.split())
        letters = sum(ch.isalpha() for ch in text)
        if letters >= 4 and letters >= len(text) * 0.5 and text not in seen and not _NOISE_RE.match(text):
            seen.add(text)
            out.append(text)
    kind = "Документ Word" if path.suffix.lower() == ".doc" else "Таблица Excel"
    return f"{kind}: {path.stem}\n" + "\n".join(out)[:MAX_TEXT_CHARS]
