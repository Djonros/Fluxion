"""Sheet bending: the least inner radius, the flat length with the K-factor, the press force.

    r ≥ rmin = c·t, c by the grade and by the bend line across or along the rolling
                (Романовский, «Справочник по холодной штамповке», 90° bends, metal as delivered)
    flat length L = Σ straight parts + Σ φ·(r + K·t), K by r/t — the same table as Fluxion's
                KOMPAS parts (cad/sheet.py), so the numbers match the flat patterns
    force of air bending in a V-die F = 1,33·σв·t²·b/V; die opening V = 6…12·t;
                the inner radius it gives r ≈ 0,16·V for steel; the least flange ≈ 0,7·V
"""
from __future__ import annotations

import math
import re

from cad import sheet

from . import strength as strength_mod
from . import units
from .core import CalcError, Result, fmt
from .fields import BOOL, MATERIAL, TEXT, Field, read

KIND = "bend"
TITLE = "Гибка листа"
# grade marks (compact, lower case) -> rmin/t across, along the rolling
MIN_RADIUS = (
    (("08пс", "08кп", "оцинк", "сталь08"), (0.4, 0.8)),
    (("ст3", "сталь20", "ст20", "рифл"), (0.5, 1.0)),
    (("09г2с",), (1.0, 2.0)),
    (("45",), (1.0, 1.7)),
    (("40х",), (1.5, 2.5)),
    (("12х18", "aisi", "08х18"), (0.5, 1.0)),
    (("амг2",), (1.0, 1.5)),
    (("амг3",), (1.0, 1.5)),
    (("амг6",), (2.0, 3.0)),
    (("д16",), (2.0, 3.0)),
)
DEFAULT_MIN_RADIUS = (1.0, 2.0)
DIES = (6, 8, 10, 12, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250)

FIELDS = [
    Field("thickness", "Толщина t", units.LENGTH),
    Field("material", "Материал", MATERIAL, "лист Ст3", material_kinds=("sheet", "checker")),
    Field("radius", "Внутренний радиус r", units.LENGTH, 0.0, hint="0 — радиус от матрицы, ≈ 0,16·V"),
    Field("angle", "Угол гиба", units.ANGLE, 90.0, hint="на сколько градусов загибается полка (90 — под прямым углом)"),
    Field("flanges", "Полки по наружным размерам", TEXT, "", hint="через «;»: «40; 100; 40» — для развёртки"),
    Field("bend_length", "Длина линии гиба b", units.LENGTH, 0.0, hint="для усилия гибки, мм"),
    Field("die", "Раскрытие матрицы V", units.LENGTH, 0.0, hint="0 — подобрать 8·t"),
    Field("press", "Усилие пресса", units.FORCE, 0.0, hint="тс или кН — для проверки, хватит ли гибочного пресса"),
    Field("along", "Линия гиба вдоль проката", BOOL, False,
          hint="да — гиб вдоль направления прокатки: нужен радиус больше"),
]


def min_radius_factor(material) -> tuple[float, bool]:
    """(rmin/t, known) for the material: (across or along — picked by the caller)."""
    from cad import materials as catalog

    text = catalog.compact(material.short + " " + material.name).replace(" ", "")
    for marks, factors in MIN_RADIUS:
        if any(m in text for m in marks):
            return factors, True
    return DEFAULT_MIN_RADIUS, False


def _die_for(t: float) -> float:
    want = 8 * t if t <= 8 else 10 * t
    return min(DIES, key=lambda v: (abs(v - want), v))


def _flanges(text: str) -> list[float]:
    items = re.findall(rf"{units.NUMBER_RE}\s*(?:мм|см|м)?", text or "")
    if (text or "").strip() and not items:
        raise CalcError(f"полки «{text}»: нужны длины через «;» — «40; 100; 40»")
    return [units.parse(x, units.LENGTH) for x in items]


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    t = v["thickness"]
    if t <= 0:
        raise CalcError("толщина должна быть больше нуля")
    s = strength_mod.strength(v["material"], t, ("sheet", "checker"))
    result = Result(KIND, f"{TITLE}: {s.name} {fmt(t)} мм",
                    "холодная гибка: минимальный радиус по Романовскому, развёртка по нейтральному слою, "
                    "усилие свободной гибки в V-матрице")
    result.inputs = values.inputs("thickness", "material", "angle")
    die = v["die"] or _die_for(t)
    if not v["die"]:
        result.step("Раскрытие матрицы (ближайшая из ряда)", "V ≈ 8·t" if t <= 8 else "V ≈ 10·t", "", die, "мм")
    r = v["radius"]
    if not r:
        r = result.step("Внутренний радиус при свободной гибке", "r ≈ 0,16·V", f"0,16·{fmt(die)}", 0.16 * die, "мм")
    factors, known = min_radius_factor(s.material)
    c = factors[1] if v["along"] else factors[0]
    r_min = result.step(f"Наименьший радиус ({'вдоль' if v['along'] else 'поперёк'} проката)", "rmin = c·t",
                        f"{fmt(c)}·{fmt(t)}", c * t, "мм")
    result.check("Радиус гиба без трещин", r, r_min, "мм", 1.0, "rmin", margin=r / r_min if r_min else math.inf)
    if not known:
        result.notes.append(f"Для «{s.name}» в таблице нет коэффициента минимального радиуса — взят {fmt(c)}·t")
    k = result.step(f"Коэффициент нейтрального слоя (Романовский, r/t = {fmt(r / t)})", "K", "",
                    sheet.k_factor(r, t), "")
    phi = math.radians(v["angle"])
    result.step("Длина дуги одного гиба", "φ·(r + K·t)", f"{fmt(phi)}·({fmt(r)} + {fmt(k)}·{fmt(t)})",
                phi * (r + k * t), "мм")
    lengths = _flanges(v["flanges"])
    if len(lengths) == 1:
        raise CalcError("для развёртки нужны хотя бы две полки")
    if lengths:
        try:
            profile = sheet.build_profile(lengths, [v["angle"]] * (len(lengths) - 1), t, r, k)
        except sheet.ProfileError as exc:
            raise CalcError(str(exc)) from None
        straight = " + ".join(fmt(x) for x in profile.straights)
        result.step("Длина развёртки", "L = Σ прямых + Σ дуг",
                    f"{straight} + {len(profile.bends)}·{fmt(profile.arcs[0])}", profile.flat_length, "мм")
        short = min(lengths)
        least = 0.7 * die
        if short < least:
            result.notes.append(f"Полка {fmt(short)} мм короче 0,7·V = {fmt(least)} мм: в матрице V{fmt(die)} "
                                "её не загнуть — нужна матрица меньше (и радиус станет меньше)")
    if v["bend_length"]:
        force = result.step("Усилие гибки", "F = 1,33·σв·t²·b/V",
                            f"1,33·{fmt(s.ultimate)}·{fmt(t)}²·{fmt(v['bend_length'])}/{fmt(die)}",
                            1.33 * s.ultimate * t ** 2 * v["bend_length"] / die / 1e3, "кН")
        result.step("То же в тоннах", "F/9,81", "", force / units.G, "тс")
        if v["press"]:
            result.check("Хватит ли пресса", force, v["press"] / 1e3, "кН", 1.2, "усилие пресса")
    result.notes.append(s.describe())
    if result.checks and result.checks[0].margin < 1:
        result.suggestions.append(f"радиус не меньше {fmt(math.ceil(r_min * 2) / 2)} мм"
                                  + (" или гиб поперёк проката" if v["along"] else ""))
    return result
