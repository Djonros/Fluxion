"""Compressed members: a stand, a strut, a jack leg, a drawbar in compression.

Buckling by СП 16.13330.2017, п. 7.1.3 (calc/stability.py): λ = μ·l/i about the
weak axis, λ̄ = λ·√(σт/E), φ by the curve of the section; the margin against
the yield point (machine building):
    σ = kд·N/(φ·A) ≤ σт/[n]
with a bending moment as well — the simple interaction
    kд·N/(φ·A·σт) + kд·M/(Wx·σт) ≤ 1/[n]
λ above 120 (СП 16, табл. 32 for the main compressed members) is noted.
"""
from __future__ import annotations

from . import sections, stability, units
from . import strength as strength_mod
from .core import CalcError, Result, fmt
from .fields import CHOICE, MATERIAL, PROFILE, Field, common_fields, read

KIND = "column"
TITLE = "Сжатый стержень"
MAX_SLENDERNESS = 120

FIELDS = [
    Field("force", "Сжимающая сила N", units.FORCE),
    Field("length", "Длина l", units.LENGTH),
    Field("scheme", "Закрепление концов", CHOICE, "шарнир — шарнир", tuple(stability.MU),
          hint="μ = 1 шарниры; 2 консоль (заделка — свободный конец); 0,7 заделка — шарнир; 0,5 две заделки"),
    Field("profile", "Профиль", PROFILE, hint="труба 60х60х4, труба 57х3,5, швеллер 10, двутавр 14, круг 40"),
    Field("moment", "Изгибающий момент M", units.MOMENT, 0.0, hint="если стержень ещё и изгибается, Н·м"),
    Field("material", "Материал", MATERIAL, "Ст3"),
    *common_fields(1.5),
]


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    inputs = values.inputs("scheme", "profile", "material", "kd")
    return _calculate(v, inputs, sections.parse(v["profile"]), None, suggest=True)


def _calculate(v: dict, inputs: list, section: sections.Section, material, suggest: bool) -> Result:
    if v["length"] <= 0 or v["force"] <= 0:
        raise CalcError("нужны сжимающая сила и длина больше нуля")
    s = strength_mod.strength(material or v["material"], None, ("solid", "sheet"))
    mu = stability.MU[v["scheme"]]
    kd, n_req = v["kd"], v["n_required"]
    result = Result(KIND, f"{TITLE}: {section.name}, l = {fmt(v['length'])} мм",
                    "СП 16.13330.2017, п. 7.1.3 — коэффициент φ; запас по пределу текучести")
    result.inputs = inputs
    i_min = result.step("Наименьший радиус инерции", "i = √(Imin/A)", "", section.i_min, "мм")
    lam, lam_bar = stability.slenderness(section, v["length"], mu, s.yield_, s.elastic)
    result.step("Гибкость", "λ = μ·l/i", f"{fmt(mu)}·{fmt(v['length'])}/{fmt(i_min)}", lam, "")
    result.step("Условная гибкость", "λ̄ = λ·√(σт/E)", f"{fmt(lam)}·√({fmt(s.yield_)}/{fmt(s.elastic)})", lam_bar, "")
    phi = result.step(f"Коэффициент устойчивости (тип сечения «{section.curve}»)",
                      "φ = 0,5·(δ − √(δ² − 39,48·λ̄²))/λ̄²", "", stability.phi(lam_bar, section.curve), "")
    N = kd * v["force"]
    if v["moment"]:
        M = kd * v["moment"]
        share_n = result.step("Доля от сжатия", "kд·N/(φ·A·σт)", f"{fmt(N)}/({fmt(phi)}·{fmt(section.area)}·{fmt(s.yield_)})",
                              N / (phi * section.area * s.yield_), "")
        share_m = result.step("Доля от изгиба", "kд·M/(Wx·σт)", f"{fmt(M)}/({fmt(section.wx)}·{fmt(s.yield_)})",
                              M / (section.wx * s.yield_), "")
        result.check("Устойчивость при сжатии с изгибом", share_n + share_m, 1.0, "", n_req, "сумма долей ≤ 1")
    else:
        sigma = result.step("Напряжение с учётом φ", "σ = kд·N/(φ·A)", f"{fmt(N)}/({fmt(phi)}·{fmt(section.area)})",
                            N / (phi * section.area), "МПа")
        result.check("Устойчивость стержня", sigma, s.yield_, "МПа", n_req, "σт")
    if lam > MAX_SLENDERNESS:
        result.notes.append(f"Гибкость {fmt(lam)} больше {MAX_SLENDERNESS} (СП 16, табл. 32): стержень слишком гибкий "
                            "даже если запас есть — возьмите профиль с большим радиусом инерции")
    result.notes.append(section.describe() + f", imin {fmt(section.i_min / 10)} см")
    result.notes.append(s.describe())
    if suggest and result.margin < n_req:
        for bigger in sections.bigger(section):
            r = _calculate(v, [], bigger, None, suggest=False)
            if r.margin >= n_req:
                result.suggestions.append(f"{bigger.name} — запас {fmt(r.margin, 2)}, "
                                          f"{fmt(bigger.mass_per_m(s.density))} кг/м")
                break
        if mu > 1:
            r = _calculate({**v, "scheme": "шарнир — шарнир"}, [], section, None, suggest=False)
            result.suggestions.append(f"закрепить свободный конец (μ = 1) — запас {fmt(r.margin, 2)}")
    return result
