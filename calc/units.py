"""Quantities with units, as they are typed or said: "5 тс", "200 мм", "1,5 кН·м", "3 кН/м".

Inside the package everything is in N, mm and MPa (N/mm²); moments in N·mm,
distributed loads in N/mm.  A bare number takes the unit of the field: kN
for a force, mm for a length, N·m for a moment, kN/m for a distributed load,
MPa for a stress.  Kilogram-force and tonne-force are understood (кгс, тс;
"т" and "кг" in a force mean the same).
"""
from __future__ import annotations

import re

from .core import CalcError

G = 9.80665

FORCE, LENGTH, MOMENT, LINE_LOAD, STRESS, NUMBER, ANGLE = "force", "length", "moment", "line_load", "stress", "number", "angle"

# unit (lower case, no spaces, "·*" removed) -> factor to the base unit of the kind
_UNITS = {
    FORCE: {"н": 1.0, "n": 1.0, "кн": 1e3, "kn": 1e3, "мн": 1e6, "кгс": G, "кг": G, "kgf": G, "kg": G,
            "тс": G * 1e3, "т": G * 1e3, "t": G * 1e3},
    LENGTH: {"мм": 1.0, "mm": 1.0, "см": 10.0, "cm": 10.0, "м": 1e3, "m": 1e3},
    MOMENT: {"нмм": 1.0, "нм": 1e3, "nm": 1e3, "кнм": 1e6, "knm": 1e6, "кнмм": 1e3, "кгсм": G * 1e3,
             "кгссм": G * 10, "тсм": G * 1e6, "кгм": G * 1e3},
    LINE_LOAD: {"н/мм": 1.0, "кн/м": 1.0, "kn/m": 1.0, "н/м": 1e-3, "кгс/м": G * 1e-3, "кг/м": G * 1e-3,
                "тс/м": G, "т/м": G, "кгс/см": G * 0.1, "кн/мм": 1e3},
    STRESS: {"мпа": 1.0, "mpa": 1.0, "н/мм2": 1.0, "н/мм²": 1.0, "кгс/мм2": G, "кгс/мм²": G, "кгс/см2": G / 100,
             "кгс/см²": G / 100, "гпа": 1e3, "па": 1e-6},
    NUMBER: {"": 1.0},
    ANGLE: {"°": 1.0, "град": 1.0, "градусов": 1.0, "градуса": 1.0, "градус": 1.0, "deg": 1.0},
}
DEFAULT_UNIT = {FORCE: "кн", LENGTH: "мм", MOMENT: "нм", LINE_LOAD: "кн/м", STRESS: "мпа", NUMBER: "", ANGLE: "°"}
UNIT_TITLE = {FORCE: "кН", LENGTH: "мм", MOMENT: "Н·м", LINE_LOAD: "кН/м", STRESS: "МПа", NUMBER: "", ANGLE: "°"}

NUMBER_RE = r"[-−]?\d+(?:[.,]\d+)?"
_QUANTITY_RE = re.compile(rf"^\s*({NUMBER_RE})\s*([^\d\s].*?)?\s*$")


def number(text: str) -> float:
    return float(str(text).replace(",", ".").replace("−", "-"))


def _unit_key(unit: str) -> str:
    unit = unit.lower().replace("ё", "е").replace(" ", "").replace("·", "").replace("*", "").replace("×", "")
    unit = unit.replace("метр", "м").replace("мм.", "мм").rstrip(".")
    return unit.replace("^2", "2")


def parse(value, kind: str) -> float:
    """*value* (a number or a text with a unit) in the base unit of *kind*."""
    if isinstance(value, (int, float)):
        return float(value) * _UNITS[kind][DEFAULT_UNIT[kind]]
    text = str(value or "").strip()
    match = _QUANTITY_RE.match(text)
    if not match:
        raise CalcError(f"«{text}» — не число")
    amount = number(match.group(1))
    unit = _unit_key(match.group(2) or "") or DEFAULT_UNIT[kind]
    factors = _UNITS[kind]
    if unit not in factors:
        known = ", ".join(sorted({u for u in factors if u}))
        raise CalcError(f"«{text}»: единица «{match.group(2)}» не подходит, нужна одна из: {known}")
    return amount * factors[unit]


def kind_of(text: str) -> str | None:
    """The kind of a quantity by its unit: "20 кН" — force, "5 кН/м" — line load; None without a unit."""
    match = _QUANTITY_RE.match(str(text))
    if not match or not match.group(2):
        return None
    unit = _unit_key(match.group(2))
    for kind in (LINE_LOAD, MOMENT, FORCE, LENGTH, STRESS):
        if unit in _UNITS[kind]:
            return kind
    return None
