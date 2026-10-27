"""The result of a calculation: inputs, steps with formulas, checks, verdict, suggestions.

Every calculation of the package returns a ``Result``.  A step is one line of
the calculation as an engineer writes it: the formula, the numbers put into
it and the value.  A check compares an actual value with its limit; its
margin is limit / actual, and the verdict of the result is its worst check:

    margin < 1          — не проходит
    margin < required   — мало
    margin > 3·required — с большим запасом, можно облегчить
    otherwise           — проходит
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

FAIL, LOW, OK, EXCESS = "fail", "low", "ok", "excess"
STATUS_TEXT = {FAIL: "не проходит", LOW: "мало", OK: "проходит", EXCESS: "с большим запасом"}
EXCESS_FACTOR = 3.0


class CalcError(ValueError):
    """The input cannot be calculated: says what is wrong in words for the user."""


def fmt(value: float, digits: int = 3) -> str:
    """A number as an engineer writes it: three significant digits, a decimal comma, no "e"."""
    if value is None or isinstance(value, str):
        return str(value or "")
    if math.isinf(value):
        return "∞"
    if value == 0:
        return "0"
    magnitude = math.floor(math.log10(abs(value)))
    decimals = max(0, digits - 1 - magnitude)
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",").replace("-", "−")


@dataclass
class Step:
    title: str
    formula: str
    substitution: str
    value: float
    unit: str = ""

    def text(self) -> str:
        sub = f" = {self.substitution}" if self.substitution else ""
        return f"{self.title}: {self.formula}{sub} = {fmt(self.value)} {self.unit}".rstrip()


@dataclass
class Check:
    title: str
    actual: float
    limit: float
    unit: str
    required: float
    margin_value: float | None = None   # given directly (fatigue safety factor), else limit / actual
    basis: str = ""                      # how the limit is set: "0,6·σт", "Rwf·γc"

    @property
    def margin(self) -> float:
        if self.margin_value is not None:
            return self.margin_value
        return math.inf if self.actual <= 0 else self.limit / self.actual

    @property
    def status(self) -> str:
        return status_of(self.margin, self.required)


def status_of(margin: float, required: float) -> str:
    if margin < 1:
        return FAIL
    if margin < required:
        return LOW
    if margin > EXCESS_FACTOR * required:
        return EXCESS
    return OK


@dataclass
class Result:
    kind: str
    title: str
    basis: str                                   # the method: "машиностроение (по пределу текучести)", "СП 16.13330"
    inputs: list[tuple[str, str]] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    tables: list[tuple[str, list[str], list[list[str]]]] = field(default_factory=list)  # title, header, rows
    diagram: dict | None = None   # beams: {"x": [...], "M": [...], "Q": [...], "f": [...], "supports": [...]}

    def step(self, title: str, formula: str, substitution: str, value: float, unit: str = "") -> float:
        self.steps.append(Step(title, formula, substitution, value, unit))
        return value

    def check(self, title: str, actual: float, limit: float, unit: str, required: float,
              basis: str = "", margin: float | None = None) -> Check:
        item = Check(title, actual, limit, unit, required, margin, basis)
        self.checks.append(item)
        return item

    @property
    def governing(self) -> Check | None:
        """The check with the smallest margin relative to what is required."""
        if not self.checks:
            return None
        return min(self.checks, key=lambda c: c.margin / c.required)

    @property
    def margin(self) -> float:
        check = self.governing
        return check.margin if check else math.inf

    @property
    def status(self) -> str:
        check = self.governing
        if check is None:
            return OK
        statuses = {c.status for c in self.checks}
        for status in (FAIL, LOW):
            if status in statuses:
                return status
        return EXCESS if statuses == {EXCESS} else OK

    def verdict(self) -> str:
        """«запас 1,1 — мало (прочность шва): …» — the line the chat and the window start with."""
        check = self.governing
        if check is None:
            return "Проверок нет."
        text = f"запас {fmt(check.margin, 2)} — {STATUS_TEXT[self.status]} ({check.title.lower()}"
        if self.status in (LOW, OK) or check.status in (FAIL, LOW):
            text += f", нужно не меньше {fmt(check.required, 2)}"
        text += ")"
        if self.suggestions and self.status in (FAIL, LOW, EXCESS):
            text += ": " + "; ".join(self.suggestions[:2])
        return text[:1].upper() + text[1:]

    def text(self) -> str:
        """Plain text for the chat and the clipboard."""
        lines = [f"{self.title} — {self.verdict()}", f"Методика: {self.basis}."]
        if self.inputs:
            lines.append("Исходные данные: " + "; ".join(f"{k} {v}" for k, v in self.inputs) + ".")
        lines.append("Расчёт:")
        lines += [f"• {s.text()}" for s in self.steps]
        lines.append("Проверки:")
        for c in self.checks:
            if c.margin_value is None:
                lines.append(f"• {c.title}: {fmt(c.actual)} {c.unit} при пределе {fmt(c.limit)} {c.unit}"
                             f"{f' ({c.basis})' if c.basis else ''} — запас {fmt(c.margin, 2)}, {STATUS_TEXT[c.status]}")
            else:
                lines.append(f"• {c.title}: запас {fmt(c.margin, 2)} при нужном {fmt(c.required, 2)}"
                             f" — {STATUS_TEXT[c.status]}")
        if self.suggestions:
            lines.append("Что можно сделать: " + "; ".join(self.suggestions) + ".")
        lines += [f"⚠ {n}" for n in self.notes]
        return "\n".join(lines)


def round_up(value: float, step: float) -> float:
    return math.ceil(value / step - 1e-9) * step
