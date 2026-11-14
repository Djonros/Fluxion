"""The sheet a detail is cut from, as the plant writes it: «Лист 8 (09Г2С)» in an order,
«8 09Г2С», «4 Чеч-цаСт3», «1,5 Ст3» in the list of the remnants.

A detail and a sheet go together when the thickness, the grade and the kind (plain or
checker plate «чечевица») are the same. Grades are compared without spaces, case and
the deoxidation of a plain carbon steel: «Ст3», «3пс», «Ст3сп» are one «СТ3».
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STEEL_DENSITY = 7.85  # g/cm³

_CHECKER_RE = re.compile(r"чеч-ца|чечевиц\w*|чеч\w*|рифл\w*|ромб\w*", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_PLAIN_CARBON_RE = re.compile(r"^(?:СТ)?(\d)(?:ПС|СП|КП|ГПС|ГСП)?$")
# Latin letters typed in a Cyrillic grade (09Г2C with a Latin C, 10XCНД)
_LOOKALIKE = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def normalize_grade(text: str) -> str:
    """«09г2с» → «09Г2С», «3пс» / «Ст3сп» / «ст 3» → «СТ3», «» → «»."""
    grade = re.sub(r"[\s\-_.]", "", text or "").upper()
    if re.search("[А-ЯЁ]", grade):  # a Cyrillic grade with Latin look-alikes in it
        grade = grade.translate(_LOOKALIKE)
    match = _PLAIN_CARBON_RE.match(grade)
    if match:
        return "СТ" + match.group(1)
    return grade


@dataclass(frozen=True)
class SheetMaterial:
    thickness: float  # mm; 0 — unknown
    grade: str  # normalized; "" — unknown
    checker: bool = False  # checker plate «чечевица»

    def label(self) -> str:
        """«8 09Г2С», «3 Ст3 чечевица» — as a line of the remnants list."""
        grade = self.grade.replace("СТ", "Ст", 1) if self.grade.startswith("СТ") else self.grade
        parts = [f"{self.thickness:g}".replace(".", ",")] + ([grade] if grade else [])
        if self.checker:
            parts.append("чечевица")
        return " ".join(parts)

    def matches(self, other: SheetMaterial, any_grade: bool = False) -> bool:
        if abs(self.thickness - other.thickness) > 1e-6 or self.checker != other.checker:
            return False
        return any_grade or (self.grade != "" and self.grade == other.grade)


def parse(text) -> SheetMaterial | None:
    """The sheet of a cell: «Лист 8 (09Г2С)», «Лист 3 (3пс) чечевица», «8  10ХСНД», «4 Чеч-цаСт3»,
    «Лист 6 ГОСТ 19903-2015 / 09Г2С ГОСТ 19281-2014»; None when there is no thickness."""
    if text is None:
        return None
    raw = str(text).replace("\n", " ").strip()
    if not raw:
        return None
    checker = bool(_CHECKER_RE.search(raw))
    rest = _CHECKER_RE.sub(" ", raw)
    rest = re.sub(r"ГОСТ\s*[\d.\-–]+", " ", rest, flags=re.IGNORECASE)  # the standards are no grade
    rest = re.sub(r"лист\w*|\$d|мм\b", " ", rest, flags=re.IGNORECASE)
    aisi = re.search(r"(?i)aisi\s*\d+\w*", rest)  # «AISI 430»: its number is no thickness
    if aisi:
        rest = rest[:aisi.start()] + " " + rest[aisi.end():]
    number = _NUMBER_RE.search(rest)
    if not number:
        return None
    thickness = float(number.group().replace(",", "."))
    if not 0.1 <= thickness <= 300:
        return None
    rest = rest[:number.start()] + " " + rest[number.end():]
    inside = re.search(r"\(([^)]*)\)", rest)
    candidate = inside.group(1) if inside and inside.group(1).strip() else rest
    words = [w for w in re.split(r"[\s/(),;]+", candidate)
             if re.search(r"\d", w) or re.fullmatch(r"(?i)ст\d\w*", w)]
    grade = normalize_grade(aisi.group() if aisi else words[0] if words else "")
    return SheetMaterial(thickness, grade, checker)


def density(material: SheetMaterial) -> float:
    """g/cm³ of the grade from the catalog of materials, steel by default."""
    if not material.grade:
        return STEEL_DENSITY
    try:
        from cad import materials as catalog

        found = catalog.find(material.grade)
        if found is not None:
            return found.density
    except Exception:  # an unreadable catalog of the user: steel
        pass
    return STEEL_DENSITY
