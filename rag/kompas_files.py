"""Text of KOMPAS-3D files without KOMPAS: a card for the design archive index.

A KOMPAS file (.cdw, .spw, .m3d, .a3d, .frw — version 16 and later) is a zip;
its ``MetaProductInfo`` is an XML of the product: the object of the document
itself and its components with designation, name, material, mass, position and
count. The file is only read: nothing is opened in KOMPAS and nothing is
written. The technical requirements and the texts on the drawing field are in
a binary part and are not read here.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

KOMPAS_EXTENSIONS = {".cdw", ".spw", ".m3d", ".a3d", ".frw"}
KIND_TITLES = {".cdw": "Чертёж", ".spw": "Спецификация", ".m3d": "Деталь (модель)", ".a3d": "Сборка (модель)",
               ".frw": "Фрагмент"}
MAX_MEMBER_BYTES = 64 * 1024 * 1024  # one unpacked part: guards against a broken or hostile file
MAX_COMPONENTS = 400  # rows of a big specification in one card
# properties of the document itself worth a line in the card, by their unique names
OWN_PROPERTIES = (("stampAuthor", "Разработал"), ("checkedBy", "Проверил"), ("author", "Автор"),
                  ("organization", "Организация"), ("comment", "Комментарий"), ("format", "Формат"))
_FRACTION_RE = re.compile(r"\$d(.*?)(?:;(.*?))?\$", re.S)
_DATE_RE = re.compile(r"^CreateData=(\d{2})\.(\d{2})\.(\d{4})", re.M)
ASSEMBLY_EXTENSIONS = {".a3d", ".spw"}  # their "material" is the KOMPAS default, not a material


@dataclass
class KompasCard:
    text: str
    created: str = ""  # "12.12.2023": when the file was made in KOMPAS (the file date is the sync date)
    year: int = 0


class KompasFileError(RuntimeError):
    """Not a KOMPAS file of version 16+ or a damaged one."""


def is_kompas(path: str | Path) -> bool:
    return Path(path).suffix.lower() in KOMPAS_EXTENSIONS


def material_text(raw: str) -> str:
    """"Лист$dБ-ПУ-О-8 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$" -> "Лист Б-ПУ-О-8 ГОСТ 19903-2015 / 345-09Г2С …"."""
    raw = (raw or "").strip()
    match = _FRACTION_RE.search(raw)
    if not match:
        return re.sub(r"\s+", " ", raw)
    head = raw[: match.start()].strip()
    parts = [f"{head} {match.group(1) or ''}".strip(), (match.group(2) or "").strip()]
    return re.sub(r"\s+", " ", " / ".join(p for p in parts if p))


def _read_xml(archive: zipfile.ZipFile, name: str):
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_MEMBER_BYTES:
        raise KompasFileError(f"{name}: слишком большой раздел")
    data = archive.read(info)
    try:
        return ElementTree.fromstring(data.decode("utf-16") if data[:2] in (b"\xfe\xff", b"\xff\xfe") else data)
    except (ElementTree.ParseError, UnicodeDecodeError):
        return None


def _props(obj) -> dict[str, str]:
    """The direct properties of an object: {"name": "Полка", "marking": "723095.2801229", ...}."""
    out: dict[str, str] = {}
    for prop in obj.findall("property"):
        pid = prop.get("id", "")
        if pid == "marking":
            parts = {p.get("id"): (p.get("value") or "").strip() for p in prop.findall("property")}
            base = parts.get("base", "")
            if base:
                number = parts.get("embodimentNumber", "")
                doc = parts.get("documentNumber", "")
                out["marking"] = base + (f"-{number}" if number else "") + (f" {doc}" if doc else "")
        elif pid == "material":
            name = next((p.get("value", "") for p in prop.findall("property") if p.get("id") == "name"), "")
            if name:
                out["material"] = material_text(name)
        elif prop.get("value") not in (None, ""):
            out[pid] = prop.get("value", "").strip()
    count = obj.find("ownerEmbodiment/property[@id='count']")
    if count is not None and count.get("value"):
        out["count"] = count.get("value", "")
    source = obj.find("document/property[@id='fullFileName']")
    if source is not None and source.get("value"):
        out["file"] = source.get("value", "")
    return out


def _mass(value: str) -> str:
    try:
        kg = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{kg:.3f}".rstrip("0").rstrip(".") + " кг" if kg > 0 else ""


def _row(p: dict[str, str]) -> str:
    title = " ".join(x for x in (p.get("marking", ""), p.get("name", "")) if x)
    bits = [f"поз. {p['position']}" if p.get("position") else "", title]
    if p.get("count") and p["count"] not in ("1", "0"):
        bits.append(f"{p['count']} шт.")
    if p.get("material"):
        bits.append(p["material"])
    mass = _mass(p.get("mass", ""))
    if mass:
        bits.append(mass)
    return " — ".join(b for b in bits if b)


def _created(archive: zipfile.ZipFile) -> tuple[str, int]:
    try:
        data = archive.read("FileInfo")
    except KeyError:
        return "", 0
    text = data.decode("utf-16", "ignore") if data[:2] in (b"\xfe\xff", b"\xff\xfe") else data.decode("utf-8", "ignore")
    match = _DATE_RE.search(text)
    return (f"{match.group(1)}.{match.group(2)}.{match.group(3)}", int(match.group(3))) if match else ("", 0)


def card(path: str | Path) -> str:
    """The text of a KOMPAS file for the index (see ``read``)."""
    return read(path).text


def read(path: str | Path) -> KompasCard:
    """A KOMPAS file for the index: kind, designation and name, material, mass, people of
    the title block, the components and the date it was made; raises KompasFileError."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            root = _read_xml(archive, "MetaProductInfo")
            created, year = _created(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise KompasFileError(f"{path.name}: не файл КОМПАС-3D версии 16+ ({exc})") from exc
    lines = [f"{KIND_TITLES.get(path.suffix.lower(), 'Файл КОМПАС-3D')}: {path.stem}"]
    if root is None:
        return KompasCard(lines[0], created, year)
    product = root.find("product")
    objects = product.findall("infObject") if product is not None else []
    own: dict[str, str] = {}
    components: list[str] = []
    seen: set[str] = set()
    for obj in objects:
        p = _props(obj)
        if obj.get("type") == "embodiment" or (not own and obj.get("type") in ("drawing", "document")):
            for key, value in p.items():
                own.setdefault(key, value)
            continue
        if obj.get("type") == "group_obj" and (p.get("marking") or p.get("name")):
            row = _row(p)
            if row and row not in seen and len(components) < MAX_COMPONENTS:
                seen.add(row)
                components.append(row)
        elif obj.get("type") == "body" and not own.get("material") and p.get("material"):
            own["material"] = p["material"]  # a part: the material of its body
            own.setdefault("mass", p.get("mass", ""))
    title = " ".join(x for x in (own.get("marking", ""), own.get("name", "")) if x)
    if title:
        lines.append(f"Обозначение и наименование: {title}")
    if own.get("material") and path.suffix.lower() not in ASSEMBLY_EXTENSIONS:
        lines.append(f"Материал: {own['material']}")
    mass = _mass(own.get("mass", ""))
    if mass:
        lines.append(f"Масса: {mass}")
    for key, label in OWN_PROPERTIES:
        if own.get(key):
            lines.append(f"{label}: {own[key]}")
    if created:
        lines.append(f"Создан: {created}")
    if components:
        lines.append("Состав:")
        lines += [f"- {row}" for row in components]
    return KompasCard("\n".join(lines), created, year)
