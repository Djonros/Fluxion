"""«Архив КД»: a search index over a big design archive (thousands of KOMPAS files,
drawings in PDF, documents), kept apart from the index of the project.

AutoCAD drawings (DXF; DWG through the ODA File Converter) and old .doc/.xls are
read by rag/legacy_files.py, scans by the OCR of Windows (rag/ocr.py).

The archive is searched by words, not by meaning: designations, materials and
names are exact words and numbers, and a full-text index (SQLite FTS5, part of
Python) reads tens of thousands of files in minutes on any computer, with no
model — the embedding model on a processor would take a day for the same.

``ArchiveIndex.update`` walks the archive folders and reads only what is new or
changed since the last run (by size and modification time); files gone from
the disk leave the index. It reports progress, stops on request and goes on
from where it stopped: the work is committed every few files. Files are only
read (KOMPAS files as zips, rag/kompas_files.py) — nothing is opened in KOMPAS,
nothing is written next to them.

Every file carries the product (the first folder under the archive root, e.g.
"792720"), the year it was made (inside the KOMPAS file; the file date is the
date of the cloud sync) and the kind of document, so a question can be narrowed:
«материал поперечины в 714094», «где применяли 09Г2С после 2019».
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .documents import DOCUMENT_EXTENSIONS, extract_text
from .kompas_files import KIND_TITLES, KOMPAS_EXTENSIONS, read as read_kompas
from .legacy_files import LEGACY_EXTENSIONS, dwg_text, dxf_text, old_office_text
from .ocr import IMAGE_EXTENSIONS

PRO_FEATURE = "design_archive"
DB_NAME = "design_archive.sqlite"
ARCHIVE_EXTENSIONS = KOMPAS_EXTENSIONS | DOCUMENT_EXTENSIONS | LEGACY_EXTENSIONS
# copies and leftovers: "Деталь.m3d.bak", "Чертёж.cdw~", "~$Отчёт.docx", "Сборка.spw.bak.spw"
_SKIP_NAME_RE = re.compile(r"(\.bak\b|~$|^~\$|\.\$\$\$$)", re.IGNORECASE)
SKIP_DIRS = {"резервные копии fluxion", "$recycle.bin", "__pycache__", ".git"}
COMMIT_EVERY = 50  # files between commits: a stopped run loses at most this much work
CHUNK_CHARS = 1500
KIND_BY_EXT = {**KIND_TITLES, ".pdf": "PDF", ".docx": "Документ Word", ".xlsx": "Таблица Excel",
               ".pptx": "Презентация", ".dxf": "Чертёж DXF", ".dwg": "Чертёж DWG", ".doc": "Документ Word",
               ".xls": "Таблица Excel", **{ext: "Скан" for ext in IMAGE_EXTENSIONS}}
# words of a question that say nothing about what to find
STOP_WORDS = set("""а без в во где да для до же за и из или как какая какие какой каком каких ко когда кто
ли мы на над не нет ни но о об от по под при про с со так там то того тоже у уже что чтобы это этот эта
эти этих есть был была были было будет всё все всех его её их ему им нам нас вас вам мой наш ваш свой
сколько скольких почему зачем чем чём ли бы найди найти покажи скажи подскажи нужно надо можно ищу
поиск архив архиве архива году года год годах изделии изделие изделия после раньше позже начиная""".split())
# Russian endings cut off for a prefix search: "лонжерона" -> "лонжерон*"
_ENDINGS = sorted("""ами ями ого его ому ему ыми ими ой ей ий ый ая яя ое ее ые ие ом ем ах ях ов ев ам ям
ую юю а я о е ы и у ю ь""".split(), key=len, reverse=True)


@dataclass
class UpdateStats:
    files: int = 0  # in the archive folders
    indexed: int = 0  # read and stored this run
    unchanged: int = 0
    removed: int = 0
    failed: list[str] = field(default_factory=list)
    chunks: int = 0
    stopped: bool = False
    seconds: float = 0.0


@dataclass
class Filters:
    products: list[str] = field(default_factory=list)
    year_from: int = 0
    year_to: int = 0

    def describe(self) -> str:
        bits = []
        if self.products:
            bits.append("изделие " + ", ".join(self.products))
        if self.year_from and self.year_from == self.year_to:
            bits.append(f"{self.year_from} г.")
        elif self.year_from or self.year_to:
            bits.append(" ".join(b for b in (f"с {self.year_from}" if self.year_from else "",
                                              f"по {self.year_to}" if self.year_to else "") if b) + " г.")
        return ", ".join(bits)


@dataclass
class ArchiveHit:
    """A found piece of the archive, in the shape of rag.retriever.RetrievalResult."""

    content: str
    file_path: str
    name: str = ""  # place in a document: "стр. 3"
    score: float = 0.0
    start_line: int = 0
    end_line: int = 0
    node_type: str = "archive"
    rank: int = 0
    product: str = ""
    year: int = 0
    kind: str = ""


_YEAR = r"((?:19|20)\d{2})"
_AFTER_RE = re.compile(rf"(?:после|позже|с|начиная с)\s+{_YEAR}", re.IGNORECASE)
_BEFORE_RE = re.compile(rf"(?:до|раньше|по)\s+{_YEAR}", re.IGNORECASE)
_RANGE_RE = re.compile(rf"{_YEAR}\s*[-–—]\s*{_YEAR}")
_IN_RE = re.compile(rf"(?:в|во|за)\s+{_YEAR}\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[.,][0-9A-Za-zА-Яа-яЁё]+)*")


def parse_filters(query: str, products: set[str]) -> Filters:
    """Filters said in a question: the products of the archive named in it and the years
    («в 2016», «после 2019», «до 2018», «2016–2018»)."""
    found = Filters()
    words = {w.lower() for w in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", query)}
    found.products = sorted(p for p in products if p.lower() in words)
    match = _RANGE_RE.search(query)
    if match:
        found.year_from, found.year_to = sorted((int(match.group(1)), int(match.group(2))))
        return found
    after, before, within = _AFTER_RE.search(query), _BEFORE_RE.search(query), _IN_RE.search(query)
    if after:
        found.year_from = int(after.group(1)) + (1 if after.group(0).lower().startswith(("после", "позже")) else 0)
    if before:
        found.year_to = int(before.group(1)) - (1 if before.group(0).lower().startswith(("до", "раньше")) else 0)
    if within and not after and not before:
        found.year_from = found.year_to = int(within.group(1))
    return found


def _stem(word: str) -> str:
    if not re.fullmatch(r"[а-яё]+", word) or len(word) < 5:
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def match_query(query: str, filters: Filters | None = None) -> str:
    """The FTS5 query of a question: its words (without the filter words), Russian ones
    as prefixes, designations as phrases ("714094.2801094" -> "714094 2801094")."""
    skip = {p.lower() for p in (filters.products if filters else [])}
    if filters and (filters.year_from or filters.year_to):  # every year said is a filter, not a word
        skip |= set(re.findall(_YEAR, query))
    terms: list[str] = []
    for raw in _WORD_RE.findall(query.lower().replace("ё", "е")):
        if raw in STOP_WORDS or raw in skip or (len(raw) < 3 and not raw.isdigit()):
            continue
        parts = re.findall(r"[0-9a-zа-я]+", raw)
        if len(parts) > 1:  # a designation or a size: a phrase
            terms.append('"' + " ".join(parts) + '"')
        elif parts:
            word = parts[0]
            stem = _stem(word)
            terms.append(f"{stem}*" if stem != word or len(word) >= 5 and not word.isdigit() else word)
    return " OR ".join(dict.fromkeys(terms))


def _skipped(path: Path, root: Path) -> bool:
    if _SKIP_NAME_RE.search(path.name):
        return True
    return any(part.lower() in SKIP_DIRS for part in path.relative_to(root).parts[:-1])


def scan(roots: list[str | Path], images: bool = False) -> list[tuple[Path, Path]]:
    """(root, file) of every file of the archive the index reads, in a stable order;
    *images*: scans saved as pictures too (with the OCR on)."""
    extensions = ARCHIVE_EXTENSIONS | (IMAGE_EXTENSIONS if images else set())
    out = []
    for raw in roots:
        root = Path(raw)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() in extensions and path.is_file() and not _skipped(path, root):
                out.append((root, path))
    return out


def product_of(path: Path, root: Path) -> str:
    """The product a file belongs to: the first folder under the archive root ("792720")."""
    parts = path.relative_to(root).parts
    return parts[0] if len(parts) > 1 else ""


def _pdf_year(path: Path) -> int:
    try:
        from pypdf import PdfReader

        info = PdfReader(str(path)).metadata or {}
        raw = str(info.get("/CreationDate") or "")
    except Exception:
        return 0
    match = re.search(r"(19|20)\d{2}", raw)
    return int(match.group(0)) if match else 0


def read_file(path: Path, ocr: bool = False) -> tuple[str, int]:
    """(text, year made) of an archive file; with *ocr* the scans (pictures, PDF pages
    without text) are read by the OCR of Windows (rag/ocr.py); AutoCAD drawings and old
    .doc/.xls by rag/legacy_files.py. Raises DocumentError, KompasFileError, OcrError or
    LegacyFileError."""
    from . import ocr as windows_ocr

    suffix = path.suffix.lower()
    if suffix in KOMPAS_EXTENSIONS:
        card = read_kompas(path)
        return card.text, card.year
    if suffix in IMAGE_EXTENSIONS:
        return windows_ocr.text_of_image(path), 0
    if suffix == ".dxf":
        return dxf_text(path)
    if suffix == ".dwg":
        return dwg_text(path)
    if suffix in (".doc", ".xls"):
        return old_office_text(path), 0
    text = extract_text(path)
    if suffix == ".pdf":
        if ocr:
            text = windows_ocr.text_of_pdf_scans(path, text)
        return text, _pdf_year(path)
    return text, 0


def pieces(text: str, path: Path) -> list[tuple[str, str]]:
    """(text, place) pieces of a file for the index: a document by its pages, a long card
    (a big specification) by size, every piece of a card headed by its first line."""
    from .chunker import TreeSitterChunker

    language = "document" if path.suffix.lower() in DOCUMENT_EXTENSIONS else "card"
    chunks = TreeSitterChunker(max_chars=CHUNK_CHARS, overlap=150).chunk_text(text, str(path), language=language)
    header = text.split("\n", 1)[0].strip()
    out = []
    for i, chunk in enumerate(c for c in chunks if c.content.strip()):
        content = chunk.content
        if i and language == "card" and header and not content.startswith(header):
            content = f"{header} (продолжение)\n{content}"
        out.append((content, chunk.name))
    return out


class ArchiveIndex:
    """The archive index: a SQLite database with the files read and a full-text table of
    their pieces (table "chunks"), in the data folder of the app."""

    def __init__(self, data_dir: str | Path, roots: list[str] | None = None, ocr: bool | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / DB_NAME
        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY, root TEXT, product TEXT, year INTEGER, kind TEXT,
                size INTEGER, mtime INTEGER, pieces INTEGER, error TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                text, path UNINDEXED, place UNINDEXED, tokenize='unicode61 remove_diacritics 2');
        """)
        if roots is not None:
            self.roots = roots
        if ocr is not None:
            self.ocr = ocr

    def close(self) -> None:
        self.db.close()

    # ── settings ─────────────────────────────────────────────────────────
    @property
    def roots(self) -> list[str]:
        row = self.db.execute("SELECT value FROM settings WHERE key='roots'").fetchone()
        return [r for r in (row[0] if row else "").split("\n") if r]

    @roots.setter
    def roots(self, roots: list[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('roots', ?)", ("\n".join(str(r) for r in roots),))
        self.db.commit()

    @property
    def ocr(self) -> bool:
        """Read scans with the OCR of Windows (on by default where it is there)."""
        row = self.db.execute("SELECT value FROM settings WHERE key='ocr'").fetchone()
        if row:
            return row[0] == "1"
        from .ocr import available

        return available()

    @ocr.setter
    def ocr(self, on: bool) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('ocr', ?)", ("1" if on else "0",))
        self.db.commit()

    @property
    def products(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT product FROM files WHERE product != ''")}

    @property
    def file_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM files WHERE pieces > 0").fetchone()[0]

    @property
    def updated(self) -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key='updated'").fetchone()
        return row[0] if row else ""

    # ── the update ───────────────────────────────────────────────────────
    def _forget(self, path: str) -> None:
        self.db.execute("DELETE FROM chunks WHERE path = ?", (path,))
        self.db.execute("DELETE FROM files WHERE path = ?", (path,))

    def _index_one(self, root: Path, path: Path, stat, ocr: bool = False) -> int:
        key = str(path)
        self._forget(key)
        try:
            text, year = read_file(path, ocr=ocr)
        except Exception as exc:  # whatever goes wrong with one file, the archive goes on
            # remembered with its size and time: not read again until the file changes
            self.db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)",
                            (key, str(root), product_of(path, root), 0, "", stat.st_size, int(stat.st_mtime), 0,
                             str(exc)))
            raise
        rows = pieces(text, path)
        self.db.executemany("INSERT INTO chunks (text, path, place) VALUES (?,?,?)",
                            [(content, key, place) for content, place in rows])
        self.db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)",
                        (key, str(root), product_of(path, root), int(year or 0),
                         KIND_BY_EXT.get(path.suffix.lower(), path.suffix.lower()), stat.st_size,
                         int(stat.st_mtime), len(rows), ""))
        return len(rows)

    def update(self, progress: Callable[[int, int, str], None] = lambda done, total, name: None,
               stop_requested: Callable[[], bool] = lambda: False) -> UpdateStats:
        """Read what is new or changed in the archive folders, drop what is gone."""
        started = time.time()
        stats = UpdateStats()
        ocr = self.ocr
        files = scan(self.roots, images=ocr)
        stats.files = len(files)
        known = {row[0]: (row[1], row[2]) for row in self.db.execute("SELECT path, size, mtime FROM files")}
        on_disk = {str(p) for _, p in files}
        for gone in [p for p in known if p not in on_disk]:
            self._forget(gone)
            stats.removed += 1
        self.db.commit()
        todo = []
        for root, path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            if known.get(str(path)) == (stat.st_size, int(stat.st_mtime)):
                stats.unchanged += 1
            else:
                todo.append((root, path, stat))
        for done, (root, path, stat) in enumerate(todo, 1):
            if stop_requested():
                stats.stopped = True
                break
            progress(done, len(todo), path.name)
            try:
                stats.chunks += self._index_one(root, path, stat, ocr)
                stats.indexed += 1
            except Exception as exc:
                stats.failed.append(f"{path.name}: {exc}")
            if done % COMMIT_EVERY == 0:
                self.db.commit()
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('updated', ?)", (time.strftime("%d.%m.%Y %H:%M"),))
        self.db.commit()
        stats.seconds = time.time() - started
        return stats

    def clear(self) -> None:
        self.db.execute("DELETE FROM chunks")
        self.db.execute("DELETE FROM files")
        self.db.commit()

    # ── search ───────────────────────────────────────────────────────────
    def search(self, query: str, top_k: int = 5, filters: Filters | None = None) -> list[ArchiveHit]:
        """Pieces of the archive for a question, best first; *filters* default to those
        said in it (products, years)."""
        filters = filters if filters is not None else parse_filters(query, self.products)
        match = match_query(query, filters)
        if not match:
            return []
        where, args = ["chunks MATCH ?"], [match]
        if filters.products:
            where.append(f"f.product IN ({','.join('?' * len(filters.products))})")
            args += filters.products
        if filters.year_from:
            where.append("f.year >= ?")
            args.append(filters.year_from)
        if filters.year_to:
            where.append("f.year <= ? AND f.year > 0")
            args.append(filters.year_to)
        sql = (f"SELECT chunks.text, chunks.path, chunks.place, bm25(chunks) AS rank, f.product, f.year, f.kind "
               f"FROM chunks JOIN files f ON f.path = chunks.path WHERE {' AND '.join(where)} "
               f"ORDER BY rank LIMIT ?")
        try:
            rows = self.db.execute(sql, (*args, top_k * 10)).fetchall()
        except sqlite3.OperationalError:  # a query FTS5 cannot parse: nothing found rather than an error
            return []
        hits: list[ArchiveHit] = []
        seen: set[str] = set()
        for text, path, place, rank, product, year, kind in rows:
            # one piece per file, and one of the copies of a file kept in several folders
            copy = " ".join(text.split("\n", 1)[-1].split())[:400]
            if path in seen or copy in seen:
                continue
            seen.update((path, copy))
            hits.append(ArchiveHit(text, path, place or "", -float(rank), product=product or "", year=year or 0,
                                   kind=kind or "", rank=len(hits)))
            if len(hits) >= top_k:
                break
        return hits
