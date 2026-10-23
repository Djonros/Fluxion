"""The structure of a KOMPAS-3D assembly: components, materials, masses, areas.

The assembly is opened read-only and invisible and closed without saving:
Fluxion never changes the designer's model. ``read`` returns a tree of
``Component``; cad/report.py turns it into the summary statement and the
metal report. KOMPAS gives mass in grams, volume in cm³ and area in cm².
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ASSEMBLY, DETAIL, STANDARD, OTHER = "assembly", "detail", "standard", "other"
KIND_TITLES = {ASSEMBLY: "Сборочная единица", DETAIL: "Деталь", STANDARD: "Стандартное изделие",
               OTHER: "Прочее изделие"}
OTHER_SECTION = "Прочие изделия"  # bought units: not made, so not in the metal report or the remarks

# bought units a designer often leaves in "Детали" or "Сборочные единицы": the name
# starts with what it is (a grating deck ordered outside by its drawing, a lamp, a
# valve, a hose...). "Кронштейн фонаря" or "Кожух фонаря" start otherwise: made here.
# An axle only with its load ("Ось 35т …"): a plain "Ось" is a pin made here.
PURCHASED_RE = re.compile(
    r"^\s*(настил\w*\s+решетч|решетч\w*\s+настил|полуось|ось\s+\d+(?:[.,]\d+)?\s*т\b|шина|шины|колесо|колеса|"
    r"рвд\b|рукав|шланг|фитинг|клапан|кран\b|розетка|фонарь|зажим|отбойник|подшипник|манжета|"
    r"катушка|датчик|кабель|разъем|выключатель|аккумулятор)",
    re.IGNORECASE,
)


def purchased_by_name(c: "Component") -> bool:
    """A bought unit by its name, though it is not in «Прочие изделия»."""
    return (c.kind in (ASSEMBLY, DETAIL) and c.section != OTHER_SECTION
            and bool(PURCHASED_RE.search((c.name or "").lower().replace("ё", "е"))))


@dataclass
class Component:
    kind: str  # ASSEMBLY | DETAIL | STANDARD | OTHER
    marking: str = ""  # designation ("792720.2801201")
    name: str = ""
    material: str = ""  # as KOMPAS keeps it, with $d...$ fractions
    mass_g: float = 0.0
    area_cm2: float = 0.0
    file: str = ""
    section: str = ""  # "Раздел спецификации" in KOMPAS ("Детали", "Прочие изделия", ...)
    position: str = ""  # "Позиция" in the specification of the assembly it is in
    guessed: bool = False  # taken as bought by its name (cad.report warns the designer)
    # the build tree of a detail: "Эскиз:2", "Элемент выдавливания:1"... (cad.report counts the work)
    features: list[str] = field(default_factory=list)
    children: list["Component"] = field(default_factory=list)

    @property
    def mass_kg(self) -> float:
        return self.mass_g / 1000

    @property
    def area_m2(self) -> float:
        return self.area_cm2 / 10000

    @property
    def key(self) -> tuple:
        """Instances with the same key are one item of the statement. A local part
        (made inside the assembly) has a file of its own per instance, "...>Деталь
        (локальная) 3.m3d": its file is not compared."""
        file = "" if ">" in self.file else self.file.lower()
        return (self.kind, self.marking, self.name, self.material, round(self.mass_g, 1), file)

    @property
    def title(self) -> str:
        return " ".join(part for part in (self.marking, self.name) if part) or "(без обозначения и наименования)"


_FRACTION_RE = re.compile(r"\$d(.*?)(?:;(.*?))?\$", re.S)


def material_parts(raw: str) -> tuple[str, str]:
    """(sortament, grade) of a KOMPAS material: "Лист$dБ-ПУ-О-10 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$"
    -> ("Лист Б-ПУ-О-10 ГОСТ 19903-2015", "345-09Г2С ГОСТ 17066-94"); a plain grade -> ("", grade)."""
    raw = (raw or "").strip()
    match = _FRACTION_RE.search(raw)
    if not match:
        return "", re.sub(r"\s+", " ", raw)
    head = raw[: match.start()].strip()
    numerator = (match.group(1) or "").strip()
    sortament = re.sub(r"\s+", " ", f"{head} {numerator}".strip())
    grade = re.sub(r"\s+", " ", (match.group(2) or "").strip())
    return sortament, grade


def material_text(raw: str) -> str:
    """A KOMPAS material in one line: "Лист Б-ПУ-О-10 ГОСТ 19903-2015 / 345-09Г2С ГОСТ 17066-94"."""
    sortament, grade = material_parts(raw)
    return " / ".join(part for part in (sortament, grade) if part)


# the material KOMPAS-3D gives a new part: left as it is, it means "not chosen"
DEFAULT_MATERIAL_RE = re.compile(r"^сталь\s*10(\s+гост\s*1050(-\d+)?)?$", re.IGNORECASE)


def no_material(raw: str) -> bool:
    """No material, "Без указания материала" or the KOMPAS default «Сталь 10 ГОСТ 1050»
    (a sheet or a profile of grade Сталь 10 was chosen on purpose and counts)."""
    sortament, grade = material_parts(raw)
    text = material_text(raw).lower()
    if not text or "без указания" in text:
        return True
    return not sortament and bool(DEFAULT_MATERIAL_RE.match(grade.strip()))


# ── reading from KOMPAS ──────────────────────────────────────────────────────


@contextmanager
def opened(app, path: str | Path):
    """A KOMPAS document opened invisible and read-only, closed without saving.
    A document the designer already has open (maybe with unsaved changes) is
    read as it is and never closed."""
    path = Path(path)
    for open_doc in _get(app, "Documents", None) or []:
        if str(_get(open_doc, "PathName", "")).lower() == str(path).lower():
            yield open_doc
            return
    old_hide = _get(app, "HideMessage", None)
    try:
        app.HideMessage = 1  # ksHideMessageNo: a dialog must not stop the reading
    except Exception:
        pass
    doc = None
    try:
        doc = app.Documents.Open(str(path), False, True)  # invisible, read-only
        if doc is None:
            raise AssemblyError(f"КОМПАС-3D не открыл {path.name}.")
        yield doc
    finally:
        if doc is not None:
            try:
                doc.Close(0)  # kdDoNotSaveChanges
            except Exception:
                pass
        if old_hide is not None:
            try:
                app.HideMessage = old_hide
            except Exception:
                pass


class AssemblyError(RuntimeError):
    """KOMPAS could not open or read the assembly; the message is for the user."""


def _get(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


class _Properties:
    """Properties of a component from the property manager of KOMPAS:
    "Раздел спецификации" and "Позиция"."""

    NAMES = ("Раздел спецификации", "Позиция")

    def __init__(self, app, doc, cast):
        self.cast, self.props = cast, {}
        try:
            mng = cast(app, "IPropertyMng")
            for prop in mng.GetProperties(doc) or []:
                prop = cast(prop, "IProperty")
                if prop.Name in self.NAMES:
                    self.props[prop.Name] = prop
        except Exception:
            self.props = {}

    def __call__(self, part) -> dict[str, str]:
        out = {}
        if not self.props:
            return out
        try:
            keeper = self.cast(part, "IPropertyKeeper")
        except Exception:
            return out
        for name, prop in self.props.items():
            try:
                result = keeper.GetPropertyValue(prop, None, True)
                value = result[1] if isinstance(result, tuple) else result
            except Exception:
                value = None
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            out[name] = str(value).strip() if value not in (None, "") else ""
        return out


# what every model has before the designer starts: not work
_DEFAULT_FEATURES = re.compile(r"^(плоскость\s+(xy|zx|zy)|ось\s+[xyz]|начало\s+координат)$", re.IGNORECASE)


def features_of(part, cast) -> list[str]:
    """The operations of a detail's build tree, without the default planes and axes."""
    try:
        subs = cast(part, "IFeature7").SubFeatures(1, True, False) or []
    except Exception:
        return []
    subs = subs if isinstance(subs, (list, tuple)) else [subs]
    out = []
    for s in subs:
        try:
            name = str(cast(s, "IFeature7").Name or "").strip()
        except Exception:
            continue
        if name and not _DEFAULT_FEATURES.match(name):
            out.append(name)
    return out


def _component(part, cast, with_area: bool, properties=None, features=None) -> Component:
    standard = bool(_get(part, "Standard", False))
    detail = bool(_get(part, "Detail", False))
    props = properties(part) if properties is not None else {}
    spec_section = props.get("Раздел спецификации", "")
    if standard:
        kind = STANDARD
    elif spec_section == OTHER_SECTION:
        kind = OTHER
    else:
        kind = DETAIL if detail else ASSEMBLY
    area = 0.0
    if with_area and kind == DETAIL:
        try:
            area = float(cast(part, "IMassInertiaParam7").Area)
        except Exception:
            area = 0.0
    return Component(
        kind=kind,
        marking=str(_get(part, "Marking", "") or "").strip(),
        name=str(_get(part, "Name", "") or "").strip(),
        material=str(_get(part, "Material", "") or "").strip(),
        mass_g=float(_get(part, "Mass", 0.0) or 0.0),
        area_cm2=area,
        file=str(_get(part, "FileName", "") or ""),
        section=spec_section,
        position=props.get("Позиция", ""),
        features=features(part) if features is not None and kind == DETAIL else [],
    )


def _walk(part, cast, with_area: bool, properties=None, features=None) -> list[Component]:
    out = []
    for child in _get(part, "Parts", None) or []:
        child = cast(child, "IPart7")
        if bool(_get(child, "Excluded", False)):
            continue  # excluded from the calculation in KOMPAS
        component = _component(child, cast, with_area, properties, features)
        if component.kind == ASSEMBLY:  # a bought unit (OTHER) is one item: its insides are not read
            component.children = _walk(child, cast, with_area, properties, features)
        out.append(component)
    return out


def read(path: str | Path, with_area: bool = True, log=print) -> Component:
    """The tree of the assembly at *path* (opened read-only, closed unsaved)."""
    path = Path(path)
    if not path.is_file():
        raise AssemblyError(f"Файл не найден: {path}")
    if path.suffix.lower() != ".a3d":
        raise AssemblyError("Нужна сборка КОМПАС-3D (.a3d).")
    try:
        from win32com.client import CastTo, Dispatch
    except ImportError as exc:
        raise AssemblyError("Нужен пакет pywin32: pip install pywin32") from exc
    try:
        app = Dispatch("Kompas.Application.7")
    except Exception as exc:
        raise AssemblyError(f"КОМПАС-3D не запускается: {exc}") from exc
    log(f"Открываю сборку только для чтения: {path.name}")
    try:
        with opened(app, path) as doc:
            top_part = CastTo(CastTo(doc, "IKompasDocument3D").TopPart, "IPart7")
            top = _component(top_part, CastTo, with_area=False)
            top.kind = ASSEMBLY
            top.file = top.file or str(path)
            log("Читаю дерево сборки…")
            seen: dict[str, list[str]] = {}

            def features(part) -> list[str]:  # read once per detail file: copies are not work
                key = str(_get(part, "FileName", "") or "").lower()
                if key and ">" not in key and key in seen:
                    return seen[key]
                names = features_of(part, CastTo)
                if key and ">" not in key:
                    seen[key] = names
                return names

            top.children = _walk(top_part, CastTo, with_area, _Properties(app, doc, CastTo), features)
            if with_area:
                try:
                    top.area_cm2 = float(CastTo(top_part, "IMassInertiaParam7").Area)
                except Exception:
                    pass
            return top
    except AssemblyError:
        raise
    except Exception as exc:
        raise AssemblyError(f"КОМПАС-3D не смог прочитать сборку: {exc}") from exc
