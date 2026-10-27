"""The inputs of a calculation, described once for the window, the chat and the report.

A ``Field`` knows its title, its kind (a quantity with a unit, a choice, a
material, a profile, a yes/no, a text), its default and whether it is needed.
``read`` turns raw values — numbers, texts with units, "да"/"нет" — into the
values the calculation takes, and says what is missing or wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import units
from .core import CalcError

CHOICE, MATERIAL, PROFILE, BOOL, TEXT = "choice", "material", "profile", "bool", "text"
BASIS_MACHINE = "машиностроение"
BASIS_SP16 = "СП 16.13330"


@dataclass(frozen=True)
class Field:
    name: str
    title: str
    kind: str                       # a units kind or CHOICE / MATERIAL / PROFILE / BOOL / TEXT
    default: object = None          # None: needed
    choices: tuple[str, ...] = ()
    hint: str = ""                  # for the chat model and the tooltip
    material_kinds: tuple[str, ...] = ()   # MATERIAL: catalog kinds offered first ("sheet")
    advanced: bool = False          # shown in the window under «Ещё», not asked in the chat

    @property
    def unit(self) -> str:
        return units.UNIT_TITLE.get(self.kind, "")

    @property
    def required(self) -> bool:
        return self.default is None


def basis_field(note: str = "") -> Field:
    return Field("basis", "Методика", CHOICE, BASIS_MACHINE, (BASIS_MACHINE, BASIS_SP16),
                 "машиностроение — запас по пределу текучести; СП 16.13330 — расчётные сопротивления" + note)


def common_fields(required_margin: float = 1.5) -> list[Field]:
    return [
        Field("kd", "Коэффициент динамичности kд", units.NUMBER, 1.0,
              hint="нагрузка умножается на kд; для прицепов по дорогам 1,5–2", advanced=False),
        Field("n_required", "Нужный запас [n]", units.NUMBER, required_margin,
              hint="машиностроение: во сколько раз предел выше напряжения", advanced=True),
    ]


_YES = {"да", "yes", "true", "1", "есть", "+", "вкл", "истина"}
_NO = {"нет", "no", "false", "0", "-", "выкл", "", "ложь"}


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _YES:
        return True
    if text in _NO:
        return False
    raise CalcError(f"«{value}» — нужно «да» или «нет»")


def choice(f: Field, value) -> str:
    text = str(value).strip().lower().replace("ё", "е")
    for choice in f.choices:
        if text == choice.lower().replace("ё", "е"):
            return choice
    for choice in f.choices:   # a start or a word of the choice: "угловой", "сп", "полуавтомат"
        low = choice.lower().replace("ё", "е")
        if text and (low.startswith(text) or text in low.split() or text in low):
            return choice
    raise CalcError(f"{f.title}: «{value}» — выберите из: {', '.join(f.choices)}")


def shown_value(f: Field, value) -> str:
    """A raw value as the report shows it: a text as typed, a bare number with the unit of the field."""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return f"{value:g} {f.unit}".strip().replace(".", ",")
    return str(value).strip()


@dataclass
class Values:
    values: dict = field(default_factory=dict)
    missing: list[Field] = field(default_factory=list)
    shown: list[tuple[str, str, str, bool]] = field(default_factory=list)  # name, title, value, given (not default)

    def inputs(self, *always: str, skip: tuple[str, ...] = ()) -> list[tuple[str, str]]:
        """The inputs for the report: the given ones and the defaults named in *always*."""
        return [(title, text) for name, title, text, given in self.shown
                if (given or name in always) and name not in skip]


def read(fields: list[Field], raw: dict, *, partial: bool = False) -> Values:
    """Raw values -> the values of the calculation (base units).  Empty fields take
    their defaults; needed ones without a value are listed in ``missing`` (and,
    unless *partial*, raise)."""
    out = Values()
    for f in fields:
        value = raw.get(f.name)
        if value in (None, "") or (isinstance(value, str) and not value.strip()):
            if f.required:
                out.missing.append(f)
                continue
            value = f.default
            given = False
        else:
            given = True
        try:
            if f.kind == CHOICE:
                converted = choice(f, value)
            elif f.kind == BOOL:
                converted = to_bool(value)
            elif f.kind in (MATERIAL, PROFILE, TEXT):
                converted = str(value).strip()
            else:
                converted = units.parse(value, f.kind)
        except CalcError as exc:
            raise CalcError(f"{f.title}: {exc}") from None
        out.values[f.name] = converted
        out.shown.append((f.name, f.title, shown_value(f, converted if f.kind in (CHOICE, BOOL) else value), given))
    if out.missing and not partial:
        raise CalcError("Не хватает: " + ", ".join(f.title.lower() for f in out.missing))
    return out
