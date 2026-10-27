"""Инженерный калькулятор: welds, bolts, beams, sheet bending, shafts — by formulas, not by the model.

Each calculation is a module with ``FIELDS`` (its inputs) and ``calculate(raw)``
that takes raw values (numbers or texts with units: "5 тс", "200 мм") and
returns a ``Result``: the steps with formulas, the checks with margins, the
verdict («запас 1,1 — мало: …») and the suggestions, each one calculated too.
The chat model only reads a question into these raw values (calc/chat.py)
and puts the result into words; it never computes.

The calculations are free; the report in Excel or PDF (calc/report.py) is
a feature of Fluxion Pro.
"""
from __future__ import annotations

from types import ModuleType

from . import beams, bending, bolts, shafts, welds
from .core import CalcError, Result, fmt

PRO_FEATURE = "calc_report"

# kind -> (tab title, module), in the order of the window
CALCS: dict[str, tuple[str, ModuleType]] = {
    welds.KIND: ("Швы", welds),
    bolts.KIND: ("Болты", bolts),
    beams.KIND: ("Балки", beams),
    bending.KIND: ("Гибка", bending),
    shafts.KIND: ("Валы", shafts),
}


def module(kind: str) -> ModuleType:
    if kind not in CALCS:
        raise CalcError(f"расчёта «{kind}» нет; есть: {', '.join(CALCS)}")
    return CALCS[kind][1]


def calculate(kind: str, raw: dict) -> Result:
    return module(kind).calculate(raw)


__all__ = ["CALCS", "PRO_FEATURE", "CalcError", "Result", "calculate", "fmt", "module"]
