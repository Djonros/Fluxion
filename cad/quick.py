"""Fast reading of a part description without the chat model.

A local 7B model on a CPU needs minutes to read a description; typical ones
("втулка 30 на 20 длиной 50, фаска 1, бронза") follow a few patterns that
rules read at once. The rules are cautious: they answer only when the part
type is clear and every number of the description went into a size (or into
the name of the material). Anything else — an unknown word with a number, two
part types, a shaft with its steps — returns None and the model reads it.
"""
from __future__ import annotations

import re

from . import materials, spec

N = r"(\d+(?:\.\d+)?)"
_BY = r"\s*(?:мм)?\s*(?:х|\*|на)\s*"  # 30х20, 30 x 20, 30*20, 30 на 20
_MM = r"(?:\s*мм)?"

_WORD_NUMBERS = {
    "один": 1, "одно": 1, "одним": 1, "одной": 1, "два": 2, "две": 2, "двумя": 2, "двух": 2, "двум": 2,
    "три": 3, "тремя": 3, "трех": 3, "четыре": 4, "четырьмя": 4, "четырех": 4,
    "пять": 5, "пятью": 5, "шесть": 6, "шестью": 6, "восемь": 8, "восемью": 8,
}
_WORD_NUMBER_RE = re.compile(r"\b(" + "|".join(_WORD_NUMBERS) + r")\b")

# part types by words; sheet parts need a word about a sheet as well
_SHEET_WORDS = re.compile(r"лист|гнут|\bs\s?\d|сгиб|оцинк|рифлен|чечевиц|жест[ья]")
_KINDS = [
    ("bushing", re.compile(r"\bвтулк|\bтрубк|\bкольц|\bпроставк")),
    ("plate", re.compile(r"\bпластин|\bкрышк|\bоснован|\bплит")),
    ("flange", re.compile(r"\bфланец|\bфланц|\bдиск")),
    ("nut", re.compile(r"\bгайк|\bшестигранник")),
    ("shaft", re.compile(r"\bвал\b|\bвала\b|\bвалик|\bось\b|\bпалец|\bцилиндр")),
    ("angle", re.compile(r"\bугол(?:ок|ка)|\bкронштейн|г-образн")),
    ("sheet_channel", re.compile(r"\bшвеллер|п-образн|п-профил")),
    ("sheet_z", re.compile(r"\bz-|\bзет|\bz\s*профил|\bz-образн")),
    ("sheet_tray", re.compile(r"\bлоток|\bлотка|\bкороб|\bкоробк|\bподдон")),
    ("sheet_shelf", re.compile(r"\bполк[аиу]\b.*отбортовк|отбортовк.*\bполк[аиу]\b")),
]


def _normalise(text: str) -> str:
    text = text.lower().replace("ё", "е").replace("×", "х")
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)  # 0,8 -> 0.8
    text = re.sub(r"(?<=\d)\s*[xх]\s*(?=\d)", "х", text)  # 50 x 30 -> 50х30 (Latin or Cyrillic)
    text = re.sub(r"(?<=\d)\s*(?:мм|mm)\b", " мм", text)
    return _WORD_NUMBER_RE.sub(lambda m: str(_WORD_NUMBERS[m.group(1)]), text)


class _Text:
    """The description with the pieces already read blanked out."""

    def __init__(self, text: str):
        self.rest = text

    def take(self, pattern: str) -> re.Match | None:
        match = re.search(pattern, self.rest)
        if match:
            start, end = match.span()
            self.rest = self.rest[:start] + " " * (end - start) + self.rest[end:]
        return match

    def numbers_left(self) -> bool:
        return bool(re.search(r"\d", self.rest))


def _f(value: str) -> float:
    number = float(value)
    return int(number) if number.is_integer() else number


def _kind(text: str) -> str | None:
    found = [key for key, pattern in _KINDS if pattern.search(text)]
    if "sheet_shelf" in found:
        found = ["sheet_shelf"]
    if "sheet_channel" in found and "angle" in found:  # "П-образный кронштейн"
        found.remove("angle")
    if len(found) != 1:
        return None  # nothing or two types: the model decides
    kind = found[0]
    sheet = bool(_SHEET_WORDS.search(text))
    if kind == "angle":
        if sheet:
            return "sheet_bracket" if re.search(r"отверст", text) else "sheet_angle"
        return "bracket" if re.search(r"толщин", text) else None  # "уголок 60х40" alone is ambiguous
    if kind.startswith("sheet_") and not sheet and not re.search(r"толщин|\bs\s?\d", text):
        return None
    return kind


# ── common pieces ────────────────────────────────────────────────────────────


def _chamfer(t: _Text, params: dict) -> None:
    m = t.take(rf"фаск\w*\s*{N}(?:\s*х\s*45\s*°?|\s*мм)?")
    if m:
        params["chamfer"] = _f(m.group(1))


def _thickness(t: _Text, params: dict, sheet: bool) -> None:
    m = t.take(rf"толщин\w*(?:\s+листа)?\s*{N}{_MM}")
    if not m and sheet:
        m = (t.take(rf"\bлист\w*\s*{N}{_MM}") or t.take(rf"\bs\s*=?\s*{N}{_MM}")
             or t.take(rf"(?:рифленк\w*|чечевиц\w*|оцинковк\w*|нержавейк\w*)\s+{N}{_MM}")
             or t.take(rf"\bиз\s+[а-я0-9]+\s+{N}\s*мм"))
    if m:
        params["thickness"] = _f(m.group(1))


def _sheet_common(t: _Text, params: dict) -> None:
    m = t.take(rf"(?:радиус\w*(?:\s+гиба|\s+сгиба)?|сгиб\w*\s+r|\br)\s*{N}{_MM}")
    if m:
        params["radius"] = _f(m.group(1))
    if t.take(r"по\s+внутренн\w*(?:\s+сторон\w*)?|внутренн\w*\s+размер\w*"):
        params["inside"] = 1
    t.take(r"по\s+наружн\w*(?:\s+сторон\w*)?")
    m = t.take(rf"\bk\s*=?\s*{N}")
    if m:
        params["k"] = _f(m.group(1))


def _length(t: _Text) -> float | None:
    m = t.take(rf"(?:длин\w*|\bl\s*=?)\s*{N}{_MM}")
    return _f(m.group(1)) if m else None


def _width(t: _Text) -> float | None:
    m = t.take(rf"ширин\w*\s*{N}{_MM}")
    return _f(m.group(1)) if m else None


# ── part types ───────────────────────────────────────────────────────────────


def _bushing(t: _Text, p: dict) -> None:
    m = t.take(rf"{N}{_BY}{N}")
    if m:
        p["outer_d"], p["inner_d"] = _f(m.group(1)), _f(m.group(2))
    else:
        m = t.take(rf"(?:наружн\w*(?:\s+диаметр\w*)?|снаружи)\s*{N}")
        if m:
            p["outer_d"] = _f(m.group(1))
        m = t.take(rf"(?:внутренн\w*(?:\s+диаметр\w*)?|внутри|отверсти\w*)\s*{N}")
        if m:
            p["inner_d"] = _f(m.group(1))
    length = _length(t)
    if length is None:
        m = t.take(rf"(?:высот\w*)\s*{N}{_MM}")
        length = _f(m.group(1)) if m else None
    if length is not None:
        p["length"] = length
    _chamfer(t, p)


def _plate(t: _Text, p: dict) -> None:
    m = t.take(rf"{N}{_BY}{N}(?:{_BY}{N})?")
    if m:
        p["length"], p["width"] = _f(m.group(1)), _f(m.group(2))
        if m.group(3):
            p["thickness"] = _f(m.group(3))
    _thickness(t, p, sheet=False)
    m = t.take(rf"в\s+торц\w*\s+(?:\d+\s+)?отверсти\w*\s*{N}{_MM}(?:\s+глубин\w*\s*{N}{_MM})?")
    if m:
        p["end_hole_d"] = _f(m.group(1))
        if m.group(2):
            p["end_hole_depth"] = _f(m.group(2))
    m = t.take(rf"центральн\w*\s+отверсти\w*\s*{N}{_MM}")
    if m:
        p["center_hole_d"] = _f(m.group(1))
    m = t.take(rf"(?:с\s+)?(?:4\s+)?отверсти\w*\s*{N}{_MM}(?:\s+по\s+углам)?")
    if m:
        p["hole_d"] = _f(m.group(1))
        m = t.take(rf"(?:в\s+)?{N}{_MM}\s+от\s+кра\w*")
        if m:
            p["hole_offset"] = _f(m.group(1))
    t.take(r"по\s+углам")
    _chamfer(t, p)


def _bolt(t: _Text) -> float | None:
    m = t.take(r"\bм\s*(\d+)")  # М10 -> clearance hole 11
    if m:
        return int(m.group(1)) + 1
    return None


def _flange(t: _Text, p: dict) -> None:
    m = t.take(rf"(?:фланец|фланц\w*|диск\w*)\s*(?:диаметр\w*\s*)?{N}{_MM}")
    if m:
        p["outer_d"] = _f(m.group(1))
    m = t.take(rf"(?:внутри|внутренн\w*(?:\s+диаметр\w*)?|центральн\w*\s+отверсти\w*)\s*{N}{_MM}")
    if m:
        p["inner_d"] = _f(m.group(1))
    _thickness(t, p, sheet=False)
    m = t.take(rf"(\d+)\s+отверсти\w*(?:\s+под\s+болт\w*)?(?:\s+диаметр\w*)?\s*(?:{N}{_MM})?")
    if m:
        p["bolt_count"] = int(m.group(1))
        bolt = _bolt(t)
        if m.group(2):
            p["bolt_d"] = _f(m.group(2))
        elif bolt:
            p["bolt_d"] = bolt
    m = t.take(rf"(?:на\s+)?окружност\w*\s*(?:диаметр\w*\s*)?{N}{_MM}")
    if m:
        p["bolt_circle_d"] = _f(m.group(1))


def _nut(t: _Text, p: dict) -> None:
    m = t.take(rf"(?:под\s+ключ|размер\w*\s+под\s+ключ|\bs)\s*{N}")
    if m:
        p["wrench_size"] = _f(m.group(1))
    m = t.take(rf"отверсти\w*\s*{N}{_MM}")
    if m:
        p["hole_d"] = _f(m.group(1))
    else:
        bolt = _bolt(t)
        if bolt:
            p["hole_d"] = bolt - 1  # a nut M8 has an 8 mm hole (threads are not modelled)
    m = t.take(rf"высот\w*\s*{N}{_MM}")
    if m:
        p["height"] = _f(m.group(1))


def _legs(t: _Text, p: dict) -> None:
    m = t.take(rf"полк\w*\s*{N}{_MM}\s*(?:и|,)\s*{N}{_MM}") or t.take(rf"{N}{_BY}{N}")
    if m:
        p["leg_a"], p["leg_b"] = _f(m.group(1)), _f(m.group(2))
    else:
        m = t.take(rf"полк\w*\s*(?:по\s*)?{N}{_MM}")
        if m:
            p["leg_a"] = _f(m.group(1))


def _bracket(t: _Text, p: dict) -> None:
    _legs(t, p)
    _thickness(t, p, sheet=False)
    width = _width(t)
    if width is not None:
        p["width"] = width


def _sheet_angle(t: _Text, p: dict) -> None:
    m = t.take(rf"(?:с\s+)?угл\w*(?:\s+между\s+полками)?\s*{N}\s*(?:градус\w*|°)")
    if m:
        p["angle"] = _f(m.group(1))
    _legs(t, p)
    _thickness(t, p, sheet=True)
    width = _width(t)
    if width is None:
        width = _length(t)
    if width is not None:
        p["width"] = width
    _sheet_common(t, p)
    m = (t.take(rf"по\s+(\d+)\s+отверсти\w*\s*(?:диаметр\w*\s*)?{N}{_MM}(?:\s+в\s+каждой\s+полке)?")
         or t.take(rf"(\d+)\s+отверсти\w*\s*(?:диаметр\w*\s*)?{N}{_MM}\s+в\s+каждой\s+полке"))
    if m:
        p["holes_a"] = p["holes_b"] = int(m.group(1))
        p["hole_d"] = _f(m.group(2))


def _sheet_channel(t: _Text, p: dict) -> None:
    m = t.take(rf"{N}{_BY}{N}{_BY}{N}")
    if m:
        p["back"], p["flange"], p["thickness"] = (_f(g) for g in m.groups())
    m = t.take(rf"(?:спинк\w*|дно|дна)\s*{N}{_MM}")
    if m:
        p["back"] = _f(m.group(1))
    m = t.take(rf"полк\w*\s*(?:по\s*)?{N}{_MM}")
    if m:
        p["flange"] = _f(m.group(1))
    _thickness(t, p, sheet=True)
    width = _width(t)
    if width is None:
        width = _length(t)
    if width is not None:
        p["width"] = width
    _sheet_common(t, p)


def _sheet_z(t: _Text, p: dict) -> None:
    m = t.take(rf"{N}\s*-\s*{N}\s*-\s*{N}")
    if m:
        p["flange"], p["web"], p["flange2"] = (_f(g) for g in m.groups())
    m = t.take(rf"полк\w*\s*(?:по\s*)?{N}{_MM}")
    if m:
        p["flange"] = _f(m.group(1))
    m = t.take(rf"(?:высот\w*|стенк\w*)\s*{N}{_MM}")
    if m:
        p["web"] = _f(m.group(1))
    _thickness(t, p, sheet=True)
    width = _width(t)
    if width is None:
        width = _length(t)
    if width is not None:
        p["width"] = width
    _sheet_common(t, p)


def _sheet_tray(t: _Text, p: dict, text: str) -> None:
    p["sides"] = 2 if re.search(r"\bлоток|\bлотка", text) else 4
    m = t.take(rf"(?:дно\s+)?{N}{_BY}{N}")
    if m:
        p["length"], p["width_b"] = _f(m.group(1)), _f(m.group(2))
    m = t.take(rf"(?:борт\w*|высот\w*)\s*{N}{_MM}")
    if m:
        p["height"] = _f(m.group(1))
    _thickness(t, p, sheet=True)
    _sheet_common(t, p)


def _sheet_shelf(t: _Text, p: dict, text: str) -> None:
    m = t.take(rf"{N}{_BY}{N}")
    if m:
        p["length"], p["depth"] = _f(m.group(1)), _f(m.group(2))
    m = t.take(rf"отбортовк\w*\s*{N}{_MM}")
    if m:
        p["flange"] = _f(m.group(1))
    p["flanges"] = 2 if re.search(r"(?:по\s+)?(?:2|обоим)\s+(?:кра|сторон)|с\s+2\s+сторон", text) else 1
    t.take(r"по\s+(?:2|обоим)\s+(?:кра|сторон)\w*|с\s+2\s+сторон\w*")
    _thickness(t, p, sheet=True)
    _sheet_common(t, p)


def _shaft(t: _Text, p: dict) -> None:
    m = t.take(rf"поперечн\w*\s+(?:сквозн\w*\s+)?отверсти\w*(?:\s+под\s+штифт)?\s*{N}{_MM}"
               rf"(?:\s+на\s+высот\w*\s*{N}{_MM})?")
    if m:
        p["pin_hole_d"] = _f(m.group(1))
        if m.group(2):
            p["pin_hole_z"] = _f(m.group(2))
    m = t.take(rf"(?:осев\w*|сквозн\w*|центральн\w*)\s+отверсти\w*\s*{N}{_MM}")
    if m:
        p["bore_d"] = _f(m.group(1))
    _chamfer(t, p)
    steps = []
    while True:  # "40 мм длиной 60, потом 30 длиной 80" — from the end face, in order
        m = t.take(rf"(?:диаметр\w*\s*|⌀\s*)?{N}{_MM}\s+(?:длин\w*|на\s+длин\w*)\s*{N}{_MM}")
        if not m:
            break
        steps.append({"d": _f(m.group(1)), "length": _f(m.group(2))})
    if steps:
        p["steps"] = steps


_READERS = {
    "bushing": _bushing, "plate": _plate, "flange": _flange, "nut": _nut, "bracket": _bracket,
    "sheet_angle": _sheet_angle, "sheet_bracket": _sheet_angle, "sheet_channel": _sheet_channel,
    "sheet_z": _sheet_z, "shaft": _shaft,
}


def read(description: str):
    """A Draft (cad.text2cad) read by the rules, or None when the model should read it."""
    from .text2cad import Draft

    text = _normalise(description)
    kind = _kind(text)
    if kind is None:
        return None
    t = _Text(text)
    params: dict = {}
    if kind == "sheet_tray":
        _sheet_tray(t, params, text)
    elif kind == "sheet_shelf":
        _sheet_shelf(t, params, text)
    else:
        _READERS[kind](t, params)
    if not params:
        return None
    # what is left may only be the material ("сталь 45", "09Г2С", "АМг6") and words
    tpl = spec.TEMPLATES[kind]
    kinds = materials.SHEET_KINDS if tpl.sheet else ("solid",)
    # found in the whole text: "рифлёнка 4" gave its number to the thickness
    material = materials.find(description, kinds=kinds)
    rest = materials.compact(t.rest)
    if material is not None:
        for synonym in material.synonyms:
            rest = materials._synonym_pattern(synonym).sub(" ", rest) if synonym.rstrip("!") else rest
    if re.search(r"\d", rest):
        return None  # a number the rules did not understand
    material_text = material.short if material is not None else ""
    if material is None:  # "из титана": the question will say this material is unknown
        m = re.search(r"(?<![а-я])из\s+(?!лист)([а-я]{4,})", text)
        material_text = m.group(1) if m else ""
    return Draft(kind, "", params, material_text)
