"""«Технологичность» of an assembly: what makes it dearer to make or to buy for, beyond
the norm control of cad/report.py.

- Fasteners: the standard items are read by their names as KOMPAS gives them
  («Болт М12-6gx40.58.016 ГОСТ 7798-70», «Гайка М12-6H.5.016 ГОСТ 5915-70»,
  «Шайба 12.01.08кп.016 ГОСТ 11371-78»): close lengths of one diameter, one type
  and diameter under different standards or strength classes — hints to bring
  the range down («проверьте»: the grip of a joint is not known here).
- Twin details: the same material, mass and area under different designations
  or files — possibly one detail drawn twice.
- Sheet details (the thickness and the bend radius of the sheet body, read by
  cad/assembly.py): an inner bend radius too small for the thickness, a body
  thicker or thinner than the sheet named in the material. These are errors of
  the model and go to the warnings of the report; the rest are advice that does
  not hold the report up.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from .assembly import Component, material_text, no_material

# types of fasteners by the first word of the name
TYPES = {"болт": "Болт", "винт": "Винт", "гайка": "Гайка", "шайба": "Шайба", "шпилька": "Шпилька",
         "шуруп": "Шуруп", "заклепка": "Заклёпка", "заклёпка": "Заклёпка", "штифт": "Штифт", "шплинт": "Шплинт"}
PLURAL = {"Болт": "Болты", "Винт": "Винты", "Гайка": "Гайки", "Шайба": "Шайбы", "Шпилька": "Шпильки",
          "Шуруп": "Шурупы", "Заклёпка": "Заклёпки", "Штифт": "Штифты", "Шплинт": "Шплинты"}
WITH_LENGTH = {"Болт", "Винт", "Шпилька", "Шуруп", "Заклёпка", "Штифт", "Шплинт"}
WITH_CLASS = {"Болт", "Винт", "Шпилька"}
# a flat washer (ГОСТ 11371), a spring one (ГОСТ 6402) and an enlarged one (ГОСТ 6958) do different jobs:
# each standard is a type of its own, never «разные стандарты»
APART_BY_STANDARD = {"Шайба"}

# М12, 2М12x1,25 (fine pitch), then the tolerance and «x40» — the length
_THREAD_RE = re.compile(
    r"(?<![а-яa-z])[мm](?P<d>\d+(?:[.,]\d+)?)(?:\s*[xх×*]\s*(?P<p>\d+(?:[.,]\d+)?)(?=(?:-\d+[a-h]{1,2})?\s*[xх×*]\s*\d))?(?:-\d+[a-h]{1,2})?"
    r"(?:\s*[xх×*]\s*(?P<l>\d+(?:,\d+)?)(?:\.(?P<cls>\d{2,3})\b)?)?", re.IGNORECASE)
# a washer, a pin or a cotter: «Шайба А.12.01…» (01 — the version), «Шайба 12 65Г…», «Штифт 8x40…»
_PLAIN_RE = re.compile(r"^\S+\s+(?:[A-ZА-Я]\d?[.\s])?(?P<d>\d+(?:[.,]\d(?!\d))?)(?:\s*[xх×*]\s*(?P<l>\d+(?:,\d+)?))?",
                       re.IGNORECASE)
_STANDARD_RE = re.compile(r"\b(ГОСТ(?:\s*Р)?(?:\s*(?:ISO|ИСО))?|ОСТ|DIN|ISO|ТУ)\s*(\d[\w.\-]*)", re.IGNORECASE)
_SIZE_AFTER_STANDARD_RE = re.compile(r"\s-\s*(\d+(?:[.,]\d+)?)\s*$")
# strength classes of bolts by the code in the name: .58 -> 5.8
_CLASSES = {"36": "3.6", "46": "4.6", "48": "4.8", "56": "5.6", "58": "5.8", "66": "6.6", "68": "6.8",
            "88": "8.8", "98": "9.8", "109": "10.9", "129": "12.9"}


def _number(text: str | None) -> float:
    return float(text.replace(",", ".")) if text else 0.0


@dataclass
class Fastener:
    type: str  # «Болт»
    diameter: float  # mm, the thread or the nominal size
    pitch: float = 0.0  # a fine pitch, 0 — the coarse one
    length: float = 0.0
    standard: str = ""  # «ГОСТ 7798-70»
    strength: str = ""  # «5.8»

    @property
    def size(self) -> str:
        d = f"{self.diameter:g}"
        if self.type in ("Шайба", "Штифт", "Шплинт") and not self.pitch:
            return d
        return f"М{d}" + (f"×{self.pitch:g}" if self.pitch else "")


def parse_fastener(name: str) -> Fastener | None:
    """A fastener read from the name of a standard item, or None (not a fastener)."""
    text = " ".join((name or "").split())
    first = text.split(" ", 1)[0].lower().rstrip(".,") if text else ""
    kind = TYPES.get(first)
    if kind is None:
        return None
    standard = ""
    match = _STANDARD_RE.search(text)
    if match:
        standard = f"{' '.join(match.group(1).upper().split())} {match.group(2)}"
    thread = _THREAD_RE.search(text)
    if thread:
        fastener = Fastener(kind, _number(thread.group("d")), _number(thread.group("p")),
                            _number(thread.group("l")) if kind in WITH_LENGTH else 0.0, standard)
        if kind in WITH_CLASS and thread.group("cls"):
            fastener.strength = _CLASSES.get(thread.group("cls"), "")
        return fastener
    plain = _PLAIN_RE.match(text)
    if plain:
        return Fastener(kind, _number(plain.group("d")), 0.0,
                        _number(plain.group("l")) if kind in WITH_LENGTH else 0.0, standard)
    tail = _SIZE_AFTER_STANDARD_RE.search(text)  # «Шайба ГОСТ 32484.5 - 20»
    if tail:
        return Fastener(kind, _number(tail.group(1)), 0.0, 0.0, standard)
    return None


@dataclass
class Advice:
    """A hint of «Технологичность»: it does not hold the report up."""

    topic: str  # «Крепёж», «Двойники деталей»
    title: str  # «Болты М12»
    message: str
    qty: int = 0  # pieces on the product
    items: tuple[str, ...] = ()  # the names (or designations) it is about

    def as_dict(self) -> dict:
        return {"topic": self.topic, "title": self.title, "message": self.message, "qty": self.qty,
                "items": list(self.items)}


@dataclass
class SizeRow:
    """A line of the fastener summary: a type and size on the product."""

    type: str
    size: str
    lengths: tuple[float, ...]
    standards: tuple[str, ...]
    positions: int
    qty: int


def mm_list(values) -> str:
    return ", ".join(f"{v:g}" for v in values)


def _close(a: float, b: float) -> bool:
    """Lengths a designer could well bring to one: 5 mm or 15 % apart."""
    return abs(a - b) <= max(5.0, 0.15 * max(a, b))


def fasteners(standard_lines) -> tuple[list[SizeRow], list[Advice]]:
    """The summary of the fasteners by type and size, and the hints to bring the range down.
    *standard_lines*: report.standard (each standard item once, with qty_product)."""
    groups: OrderedDict[tuple, list[tuple[Fastener, str, int]]] = OrderedDict()
    for line in standard_lines:
        f = parse_fastener(line.component.name)
        if f is not None and f.diameter > 0:
            apart = f.standard if f.type in APART_BY_STANDARD else ""
            groups.setdefault((f.type, f.size, apart), []).append((f, line.component.name, line.qty_product))
    rows, advice = [], []
    for (kind, size, _apart), items in sorted(groups.items(),
                                             key=lambda kv: (kv[0][0], kv[1][0][0].diameter, kv[0][2])):
        qty = sum(n for _f, _name, n in items)
        lengths = tuple(sorted({f.length for f, _name, _n in items if f.length}))
        standards = tuple(sorted({f.standard for f, _name, _n in items if f.standard}))
        rows.append(SizeRow(kind, size, lengths, standards, len(items), qty))
        names = tuple(name for _f, name, _n in items)
        title = f"{PLURAL.get(kind, kind)} {size}"
        by_length = OrderedDict((l, sum(n for f, _name, n in items if f.length == l)) for l in lengths)
        close = [(a, b) for a, b in zip(lengths, lengths[1:]) if _close(a, b)]
        if len(lengths) >= 4 or close:
            counts = ", ".join(f"{l:g} мм — {n} шт." for l, n in by_length.items())
            why = (f"близкие длины {mm_list(close[0])} мм" if close else f"{len(lengths)} разных длин")
            advice.append(Advice("Крепёж", title,
                                 f"{why}: {counts} Проверьте, можно ли оставить меньше длин — "
                                 "меньше позиций на складе и в закупке.", qty, names))
        if len(standards) > 1:
            advice.append(Advice("Крепёж", title,
                                 f"разные стандарты: {', '.join(standards)}. Если исполнение не важно, "
                                 "оставьте один.", qty, names))
        classes = sorted({f.strength for f, _name, _n in items if f.strength})
        if len(classes) > 1:
            advice.append(Advice("Крепёж", title,
                                  f"разные классы прочности: {', '.join(classes)}. Если нагрузка позволяет, "
                                  "оставьте один — меньше риск перепутать при сборке.", qty, names))
    return rows, advice


_EXECUTION_RE = re.compile(r"-\d{2,3}$")  # «792720.2801312-01»: an execution of 792720.2801312


def twins(detail_lines, mass_tolerance: float = 0.005, area_tolerance: float = 0.01) -> list[Advice]:
    """Details of the same material, mass and area under different designations or files."""
    candidates = [l for l in detail_lines
                  if l.component.mass_g > 0 and l.component.area_cm2 > 0 and not no_material(l.component.material)]
    candidates.sort(key=lambda l: (material_text(l.component.material), l.component.mass_g))
    out, used = [], set()
    for i, a in enumerate(candidates):
        if i in used:
            continue
        group = [a]
        ca = a.component
        for j in range(i + 1, len(candidates)):
            cb = candidates[j].component
            if material_text(cb.material) != material_text(ca.material):
                break
            if cb.mass_g - ca.mass_g > mass_tolerance * ca.mass_g:
                break
            if abs(cb.area_cm2 - ca.area_cm2) <= area_tolerance * ca.area_cm2:
                group.append(candidates[j])
                used.add(j)
        unique: OrderedDict[tuple, list] = OrderedDict()  # the same detail met twice is one
        for l in group:
            unique.setdefault((l.component.marking, l.component.file.lower()), []).append(l)
        bases = {_EXECUTION_RE.sub("", m) for m, _f in unique if m}
        if len(unique) < 2 or (len(bases) == 1 and all(m for m, _f in unique)):
            continue
        group = [ls[0] for ls in unique.values()]
        qty = sum(l.qty_product for ls in unique.values() for l in ls)
        titles = tuple(l.component.title for l in group)
        out.append(Advice("Двойники деталей", titles[0],
                          f"одинаковые материал ({material_text(ca.material)}), масса {ca.mass_kg:.3g} кг и "
                          f"площадь {ca.area_m2:.3g} м² у разных деталей: {'; '.join(titles)}. "
                          "Возможно, это одна деталь, нарисованная дважды: одно обозначение — одна позиция "
                          "в спецификации, один чертёж, одна партия.",
                          qty, titles))
    return out


# ── sheet details ────────────────────────────────────────────────────────────

# the least inner bend radius in thicknesses (bending across the rolling), by the material
_MIN_RADIUS = (
    (re.compile(r"д16|в95|1163|д1\b|ак\d", re.IGNORECASE), 2.5, "дюралюминий"),
    (re.compile(r"амг[56]|амг6", re.IGNORECASE), 1.5, "АМг5–АМг6"),
    (re.compile(r"65г|60с2|пружин", re.IGNORECASE), 2.0, "пружинная сталь"),
    (re.compile(r"\bамц|\bад\d|\bа5|\bм[1-3]\b|латун|л63|медь", re.IGNORECASE), 0.5, "мягкий металл"),
)
DEFAULT_MIN_RADIUS = 1.0  # carbon and low-alloy steel, stainless
THICKNESS_PROBLEM, RADIUS_PROBLEM = "Толщина листа не как в материале", "Малый радиус гиба"


def min_bend_radius(material: str, thickness: float) -> tuple[float, str]:
    """(the least inner bend radius in mm, what it is for) for a sheet of *material*."""
    text = material_text(material)
    for pattern, ratio, what in _MIN_RADIUS:
        if pattern.search(text):
            return ratio * thickness, what
    return DEFAULT_MIN_RADIUS * thickness, "сталь"


BEND_FEATURES = re.compile(r"^(сгиб|подсечк|обечайк|штамповк|жалюзи|буртик)", re.IGNORECASE)


def bent(c: Component) -> bool:
    """The detail has bends in its build tree («Сгиб», «Сгиб по линии», «Подсечка»…)."""
    return any(BEND_FEATURES.match(f) for f in c.features)


def sheet_problems(c: Component) -> list[tuple[str, str]]:
    """(problem, message) of a sheet detail: [] if it is not a sheet or is fine."""
    t = c.sheet_thickness
    if t <= 0:
        return []
    out = []
    try:
        from dxfka.utils import thickness_from_material

        named = float(thickness_from_material(c.material) or 0)
    except Exception:
        named = 0.0
    if named and abs(named - t) > 0.05:
        out.append((THICKNESS_PROBLEM,
                    f"Листовое тело толщиной {t:g} мм, а в материале — лист {named:g} мм. Металлоёмкость и пакет "
                    "на лазер берут толщину из материала. Если деталь так и задумана (лист поставлен ребром), "
                    "отметьте; иначе исправьте толщину тела или материал."))
    if c.bend_radius > 0 and bent(c):
        least, what = min_bend_radius(c.material, t)
        if c.bend_radius + 1e-6 < least:
            out.append((RADIUS_PROBLEM,
                        f"Внутренний радиус гиба {c.bend_radius:g} мм при толщине {t:g} мм ({what}): меньше "
                        f"{least:g} мм — риск трещин на сгибе. Увеличьте радиус или проверьте с технологом."))
    return out
