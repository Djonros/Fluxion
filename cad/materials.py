"""Materials catalog: what a part is made of, its density and, for sheets, the thicknesses.

The catalog is plain JSON: ``cad/materials.json`` ships with Fluxion and the
user's ``%APPDATA%\\Fluxion\\materials.json`` adds to it (an entry with the
same ``name`` replaces the shipped one).  A spreadsheet (CSV or Excel) can be
imported into the user's catalog; bad rows are listed, not fatal.

A material is recognised in free text by its synonyms ("сталь 45", "нерж",
"рифлёнка 4"): the entry whose synonyms cover the most of the text wins.
"""
from __future__ import annotations

import csv
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

BUILTIN_PATH = Path(__file__).with_name("materials.json")
KINDS = ("solid", "sheet", "checker")
SHEET_KINDS = ("sheet", "checker")


class CatalogError(ValueError):
    """A catalog entry or an imported row is not a usable material."""


@dataclass(frozen=True)
class Material:
    name: str        # as KOMPAS shows it in the part properties
    short: str       # for questions and answers
    density: float   # g/cm³
    kind: str = "solid"
    synonyms: tuple[str, ...] = ()
    thicknesses: tuple[float, ...] = ()        # sheet assortment, mm
    mass_m2: tuple[tuple[float, float], ...] = ()  # checker plate: (thickness, kg per m²)
    note: str = ""
    source: str = "builtin"
    # for the engineering calculator: (up to thickness mm or 0 — any, σт MPa, σв MPa), E in GPa
    strength: tuple[tuple[float, float, float], ...] = ()
    elastic: float = 0.0
    strength_note: str = ""

    @property
    def is_sheet(self) -> bool:
        return self.kind in SHEET_KINDS

    def density_for(self, thickness: float | None) -> float:
        """Density to give KOMPAS: for a checker plate the one that makes the
        mass of a flat piece equal to the table mass of 1 m² (kg/m² ÷ mm = g/cm³)."""
        for t, mass in self.mass_m2:
            if thickness is not None and abs(t - thickness) < 1e-6:
                return mass / t
        return self.density

    def has_thickness(self, thickness: float) -> bool:
        return not self.thicknesses or any(abs(t - thickness) < 1e-6 for t in self.thicknesses)

    def nearest_thicknesses(self, thickness: float, count: int = 3) -> list[float]:
        return sorted(sorted(self.thicknesses, key=lambda t: (abs(t - thickness), t))[:count])

    @property
    def has_strength(self) -> bool:
        return bool(self.strength)

    def strength_at(self, thickness: float | None = None) -> tuple[float, float]:
        """(σт, σв) in MPa for a part of *thickness* mm: the first row it fits, the last row beyond."""
        if not self.strength:
            raise CatalogError(f"«{self.short}»: в каталоге нет σт и σв")
        for t_max, yield_, ultimate in self.strength:
            if not t_max or thickness is None or thickness <= t_max:
                return yield_, ultimate
        return self.strength[-1][1], self.strength[-1][2]

    def to_json(self) -> dict:
        data = {"name": self.name, "short": self.short, "kind": self.kind, "density": self.density,
                "synonyms": list(self.synonyms)}
        if self.thicknesses:
            data["thicknesses"] = list(self.thicknesses)
        if self.mass_m2:
            data["mass_m2"] = {f"{t:g}": m for t, m in self.mass_m2}
        if self.note:
            data["note"] = self.note
        if self.strength:
            data["strength"] = [[t, y, u] for t, y, u in self.strength]
        if self.elastic:
            data["elastic"] = self.elastic
        if self.strength_note:
            data["strength_note"] = self.strength_note
        return data


# ── text matching ────────────────────────────────────────────────────────────

# Latin letters that look like Cyrillic ones in grades typed on a Latin layout (40X, 12X18H10T)
_LOOKALIKE = str.maketrans("xhtcaeopkmb", "хнтсаеорквв")


def compact(text: str) -> str:
    """Lower case, ё→е, no dots/hyphens, single spaces; Latin lookalikes in grades → Cyrillic."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[.\-–—_]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # only inside letter-digit grades, so that "aisi" or "ptfe" stay Latin
    return re.sub(r"\d[a-z\d]*[a-z][a-z\d]*", lambda m: m.group(0).translate(_LOOKALIKE), text)


def _synonym_pattern(synonym: str) -> re.Pattern:
    """A synonym matches with or without spaces where letters meet digits
    ("сталь45" in "сталь 45"); one starting with a letter where no letter stands
    before it ("ст3" is not in "лист 3"), one starting with a digit where no
    digit does; it must not run into another digit ("ст3" is not "ст35"), and a
    grade ending with "х" not into a number at all ("40х" is not "40 х 30");
    one ending with a letter is a word start ("нерж" in "нержавейка").
    A synonym ending with "!" never stands before a number: a bare "сталь!" is
    not "сталь 30", a grade Fluxion would have to ask about."""
    not_before_number = synonym.endswith("!")
    s = compact(synonym.rstrip("!")).replace(" ", "")
    body = r"\s*".join(re.escape(part) for part in re.findall(r"\d+|\D+", s))
    before = r"(?<!\d)" if s[:1].isdigit() else r"(?<![^\W\d_])"
    after = r"(?!\s*\d)" if not_before_number or s.endswith("х") else r"(?!\d)"
    return re.compile(before + body + after)


def _score(material: Material, text: str) -> int:
    return sum(len(compact(s.rstrip("!")).replace(" ", "")) for s in set(material.synonyms)
               if s.rstrip("!") and _synonym_pattern(s).search(text))


def find(text: str, catalog: list[Material] | None = None, kinds: tuple[str, ...] | None = None) -> Material | None:
    """The material named in *text*, or None.  With *kinds* the materials of
    those kinds are preferred (sheet grades for a sheet part), others are the
    fallback.  Ties go to the earlier entry (the user's catalog comes first)."""
    text = compact(text or "")
    if not text:
        return None
    catalog = catalog if catalog is not None else load()
    groups = [catalog] if not kinds else [[m for m in catalog if m.kind in kinds], catalog]
    for group in groups:
        best, best_score = None, 0
        for material in group:
            score = _score(material, text)
            if score > best_score:
                best, best_score = material, score
        if best is not None:
            return best
    return None


# ── loading ──────────────────────────────────────────────────────────────────

def user_catalog_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Fluxion" / "materials.json"


def _number(value, what: str) -> float:
    try:
        number = float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        raise CatalogError(f"{what}: не число ({value!r})") from None
    if not number > 0:
        raise CatalogError(f"{what}: должно быть больше нуля")
    return number


def from_json(data: dict, source: str = "builtin") -> Material:
    """One catalog entry -> Material; CatalogError says what is wrong."""
    if not isinstance(data, dict):
        raise CatalogError("запись — не объект")
    name = str(data.get("name") or "").strip()
    if not name:
        raise CatalogError("нет названия (name)")
    kind = str(data.get("kind") or "solid").strip().lower()
    if kind not in KINDS:
        raise CatalogError(f"«{name}»: тип {kind!r} — нужен solid, sheet или checker")
    density = _number(data.get("density"), f"«{name}»: плотность")
    if not 0.5 <= density <= 25:
        raise CatalogError(f"«{name}»: плотность {density:g} г/см³ не похожа на правду")
    thicknesses = tuple(sorted(_number(t, f"«{name}»: толщина") for t in data.get("thicknesses") or []))
    mass_m2 = tuple(sorted((_number(t, f"«{name}»: толщина в mass_m2"), _number(m, f"«{name}»: масса 1 м²"))
                           for t, m in (data.get("mass_m2") or {}).items()))
    if kind == "checker" and not mass_m2:
        raise CatalogError(f"«{name}»: у рифлёного листа нужна масса 1 м² (mass_m2)")
    short = str(data.get("short") or name).strip()
    synonyms = data.get("synonyms") or []
    if isinstance(synonyms, str):
        synonyms = re.split(r"[;,]\s*", synonyms)
    synonyms = tuple(s.strip() for s in synonyms if str(s).strip()) or (short,)
    strength = []
    for row in data.get("strength") or []:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise CatalogError(f"«{name}»: strength — строки [до толщины, σт, σв]")
        t_max = float(str(row[0]).replace(",", ".") or 0)
        yield_, ultimate = _number(row[1], f"«{name}»: σт"), _number(row[2], f"«{name}»: σв")
        if yield_ > ultimate:
            raise CatalogError(f"«{name}»: σт {yield_:g} больше σв {ultimate:g}")
        strength.append((t_max, yield_, ultimate))
    elastic = _number(data["elastic"], f"«{name}»: модуль упругости") if data.get("elastic") else 0.0
    return Material(name, short, density, kind, synonyms, thicknesses, mass_m2,
                    str(data.get("note") or "").strip(), source, tuple(strength), elastic,
                    str(data.get("strength_note") or "").strip())


def read_catalog(path: Path, source: str) -> tuple[list[Material], list[str]]:
    """Entries of one catalog file and the errors of the bad ones."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return [], []
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"{path}: не читается ({exc})"]
    entries = data.get("materials", []) if isinstance(data, dict) else data
    materials, errors = [], []
    for number, entry in enumerate(entries if isinstance(entries, list) else [], 1):
        try:
            materials.append(from_json(entry, source))
        except CatalogError as exc:
            errors.append(f"{Path(path).name}, запись {number}: {exc}")
    return materials, errors


def merge(user: list[Material], builtin: list[Material]) -> list[Material]:
    """The user's entries first; a user entry replaces the shipped one of the same name."""
    names = {m.name.casefold() for m in user}
    return list(user) + [m for m in builtin if m.name.casefold() not in names]


_cache: tuple[list[Material], list[str]] | None = None


def load(reload: bool = False) -> list[Material]:
    """The merged catalog (cached; ``reload=True`` reads the files again)."""
    return load_with_errors(reload)[0]


def load_with_errors(reload: bool = False) -> tuple[list[Material], list[str]]:
    global _cache
    if _cache is None or reload:
        builtin, errors = read_catalog(BUILTIN_PATH, "builtin")
        user, user_errors = read_catalog(user_catalog_path(), "user")
        _cache = (merge(user, builtin), errors + user_errors)
    return _cache


def default(kind: str = "solid") -> Material:
    """Сталь 45 for machined parts, лист Ст3 for sheet ones (or the first of that kind)."""
    catalog = load()
    wanted = "Сталь 45" if kind == "solid" else "лист Ст3"
    for material in catalog:
        if material.short == wanted:
            return material
    return next((m for m in catalog if (m.kind == "solid") == (kind == "solid")), catalog[0])


def common(kind: str = "solid") -> tuple[str, ...]:
    """A few frequent choices for the question about the material."""
    if kind == "solid":
        return ("сталь 45", "12Х18Н10Т (нержавейка)", "Д16Т (алюминий)", "латунь ЛС59-1", "капролон")
    return ("лист Ст3", "09Г2С", "оцинковка", "12Х18Н10Т (нержавейка)", "АМг2 (алюминий)", "рифлёный (чечевица)")


def save_user_catalog(materials: list[Material], path: Path | None = None) -> Path:
    path = Path(path or user_catalog_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"_help": "Свои материалы Fluxion: запись с тем же name заменяет встроенную (см. cad/materials.json).",
            "materials": [m.to_json() for m in materials]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ensure_user_catalog(path: Path | None = None) -> Path:
    """Create an empty user catalog with an example entry to copy, if there is none."""
    path = Path(path or user_catalog_path())
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        example = {"name": "Пример: Лист 10ХСНД ГОСТ 19281-2014", "short": "лист 10ХСНД", "kind": "sheet",
                   "density": 7.85, "synonyms": ["10хснд"], "thicknesses": [4, 5, 6, 8, 10]}
        data = {"_help": "Свои материалы Fluxion: запись с тем же name заменяет встроенную (см. cad/materials.json). "
                         "Удалите «_example» из имени поля, чтобы пример заработал.",
                "materials": [], "_example": example}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── import from CSV / Excel ──────────────────────────────────────────────────

# column title (lower case, without spaces/punctuation) -> field
_COLUMNS = {
    "название": "short", "наименование": "short", "материал": "short", "name": "short",
    "марка": "grade", "гост": "grade", "маркагост": "grade", "марка/гост": "grade", "grade": "grade",
    "плотность": "density", "плотностьгсм3": "density", "density": "density",
    "толщины": "thicknesses", "толщина": "thicknesses", "толщинымм": "thicknesses", "thicknesses": "thicknesses",
    "синонимы": "synonyms", "synonyms": "synonyms",
    "тип": "kind", "kind": "kind",
    "массам2": "mass_m2", "масса1м2": "mass_m2", "массам2кг": "mass_m2", "mass_m2": "mass_m2",
}
_KIND_WORDS = {"лист": "sheet", "листовой": "sheet", "sheet": "sheet", "рифленый": "checker", "рифлёный": "checker",
               "checker": "checker", "сплошной": "solid", "пруток": "solid", "solid": "solid", "": "solid"}


def _column(title: str) -> str | None:
    key = re.sub(r"[\s.,()³]+", "", title.lower().replace("ё", "е"))
    return _COLUMNS.get(key) or _COLUMNS.get(key.replace("/", ""))


def _numbers(text: str) -> list[str]:
    """"1; 1,5; 2" / "1 1.5 2" / "1, 1.5, 2" -> ["1", "1,5", "2"] (a comma inside a number is a decimal one)."""
    return [p for p in re.split(r"[;|/\s]+|,\s+", str(text or "").strip()) if p]


def row_to_material(row: dict[str, str]) -> Material:
    short = (row.get("short") or "").strip()
    if not short:
        raise CatalogError("нет названия")
    grade = (row.get("grade") or "").strip()
    name = short if not grade or grade.casefold() in short.casefold() else f"{short} {grade}"
    kind = _KIND_WORDS.get((row.get("kind") or "").strip().lower().replace("ё", "е"))
    if kind is None:
        raise CatalogError(f"«{short}»: тип {row.get('kind')!r} — нужен лист, рифлёный или сплошной")
    thicknesses = _numbers(row.get("thicknesses", ""))
    if thicknesses and kind == "solid":
        kind = "sheet"  # a list of thicknesses means a sheet
    masses = _numbers(row.get("mass_m2", ""))
    if masses and len(masses) != len(thicknesses):
        raise CatalogError(f"«{short}»: масс 1 м² должно быть столько же, сколько толщин")
    if masses:
        kind = "checker"
    synonyms = [s.strip() for s in re.split(r"[;,]", row.get("synonyms") or "") if s.strip()]
    return from_json({"name": name, "short": short, "kind": kind, "density": row.get("density"),
                      "thicknesses": thicknesses, "mass_m2": dict(zip(thicknesses, masses)),
                      "synonyms": synonyms or [short] + ([grade] if grade else [])}, "user")


def read_table(path: Path) -> list[list[str]]:
    """Rows of a CSV (; or , separated, UTF-8 or Windows-1251) or of the first sheet of an .xlsx."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return _xlsx_rows(path)
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else "", delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(text.splitlines(), dialect)]


_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")).iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{_S}t")))
        sheet = "xl/worksheets/sheet1.xml"
        if sheet not in names:
            raise CatalogError("в файле Excel нет листов")
        rows = []
        for row in ElementTree.fromstring(archive.read(sheet)).iter(f"{_S}row"):
            cells: dict[int, str] = {}
            for cell in row.iter(f"{_S}c"):
                ref = re.match(r"[A-Z]+", cell.get("r", "")) if cell.get("r") else None
                column = _column_index(ref.group(0)) if ref else len(cells)
                cells[column] = _xlsx_cell(cell, shared)
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
        return rows


def _column_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return index - 1


def _xlsx_cell(cell, shared: list[str]) -> str:
    kind = cell.get("t", "")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_S}t")).strip()
    value = cell.find(f"{_S}v")
    text = value.text if value is not None and value.text else ""
    if kind == "s" and text.isdigit():
        return shared[int(text)].strip() if int(text) < len(shared) else ""
    return text.strip()


@dataclass
class ImportResult:
    added: list[Material] = field(default_factory=list)
    replaced: list[Material] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    path: Path | None = None


def import_table(table: Path, catalog_path: Path | None = None) -> ImportResult:
    """Add the materials of a CSV/Excel table to the user's catalog; bad rows go to errors."""
    result = ImportResult()
    try:
        rows = read_table(table)
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, CatalogError) as exc:
        result.errors.append(f"Файл не читается: {exc}")
        return result
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        result.errors.append("Файл пустой.")
        return result
    header = [_column(str(c)) for c in rows[0]]
    if "short" not in header or "density" not in header:
        result.errors.append("В первой строке нужны столбцы «название» и «плотность» "
                             "(можно ещё «марка/ГОСТ», «толщины», «синонимы», «тип», «масса 1 м2»).")
        return result
    imported = []
    for number, row in enumerate(rows[1:], 2):
        fields = {key: str(value) for key, value in zip(header, row) if key}
        try:
            imported.append(row_to_material(fields))
        except CatalogError as exc:
            result.errors.append(f"строка {number}: {exc}")
    path = Path(catalog_path or user_catalog_path())
    current, errors = read_catalog(path, "user")
    result.errors += errors
    by_name = {m.name.casefold(): i for i, m in enumerate(current)}
    for material in imported:
        index = by_name.get(material.name.casefold())
        if index is None:
            by_name[material.name.casefold()] = len(current)
            current.append(material)
            result.added.append(material)
        else:
            current[index] = material
            result.replaced.append(material)
    if imported:
        result.path = save_user_catalog(current, path)
        load(reload=True)
    return result
