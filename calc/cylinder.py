"""A hydraulic cylinder: its forces, the pressure a load needs, the buckling of its rod.

    push   F₁ = p·π·D²/4,   pull   F₂ = p·π·(D² − d²)/4
    the pressure a load needs   p = kд·F/(π·D²/4)
    the rod at full extension, a pinned strut of the length between the pins
    (the end fixing as on «Стойки»): Euler Pcr = π²·E·I/(μ·L)², I = π·d⁴/64 —
    the rod alone, the cylinder tube taken as stiff (on the safe side) — and
    the stress σ = F/(π·d²/4) against σт.

The rod is loaded by what the pressure can push (the cylinder stalls against a
stop), so the buckling is checked with F₁, or with the load when it is larger.
The margins: the force against the load (≥ 1), buckling — 3,5 by default (the
usual one for cylinder rods), the rod stress — [n].
"""
from __future__ import annotations

import math

from . import stability, units
from . import strength as strength_mod
from .core import CalcError, Result, fmt
from .fields import CHOICE, MATERIAL, Field, read

KIND = "cylinder"
TITLE = "Гидроцилиндр"
# ГОСТ 6540-68: the bores and the rods of hydraulic cylinders, mm
BORES = (25, 32, 40, 50, 63, 80, 100, 110, 125, 140, 160, 180, 200, 220, 250, 280, 320)
RODS = (10, 12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40, 45, 50, 56, 63, 70, 80, 90, 100, 110, 125, 140, 160)
BUCKLING_MARGIN = 3.5

FIELDS = [
    Field("bore", "Диаметр поршня D", units.LENGTH),
    Field("rod", "Диаметр штока d", units.LENGTH),
    Field("pressure", "Давление p", units.STRESS, hint="МПа или бар: 16 МПа = 160 бар"),
    Field("load", "Нагрузка на цилиндр F", units.FORCE, 0.0, hint="если задана — какое давление она требует"),
    Field("length", "Длина между пальцами при полном вылете L", units.LENGTH, 0.0,
          hint="для проверки штока на продольный изгиб; 0 — не проверять"),
    Field("scheme", "Закрепление", CHOICE, "шарнир — шарнир", tuple(stability.MU),
          hint="цилиндр на двух проушинах — шарнир — шарнир"),
    Field("material", "Материал штока", MATERIAL, "Сталь 45"),
    Field("kd", "Коэффициент динамичности kд", units.NUMBER, 1.0, hint="к нагрузке"),
    Field("n_required", "Нужный запас [n]", units.NUMBER, 1.5, advanced=True),
    Field("n_buckling", "Нужный запас устойчивости штока", units.NUMBER, BUCKLING_MARGIN, advanced=True),
]


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    inputs = values.inputs("scheme", "material")
    return _calculate(v, inputs, suggest=True)


def _calculate(v: dict, inputs: list, suggest: bool) -> Result:
    D, d, p = v["bore"], v["rod"], v["pressure"]
    if D <= 0 or d <= 0 or p <= 0:
        raise CalcError("нужны диаметры поршня и штока и давление больше нуля")
    if d >= D:
        raise CalcError(f"шток ⌀{fmt(d)} не меньше поршня ⌀{fmt(D)}")
    rod = strength_mod.strength(v["material"], d, ("solid",))
    result = Result(KIND, f"{TITLE} {fmt(D)}×{fmt(d)} мм, {fmt(p)} МПа ({fmt(p * 10)} бар)",
                    "усилия по давлению; шток — устойчивость по Эйлеру, прочность по пределу текучести")
    result.inputs = inputs
    piston = math.pi * D * D / 4
    annulus = math.pi * (D * D - d * d) / 4
    push = result.step("Усилие толкания", "F₁ = p·π·D²/4", f"{fmt(p)}·{fmt(piston)}", p * piston / 1e3, "кН") * 1e3
    pull = result.step("Усилие тяги", "F₂ = p·π·(D² − d²)/4", f"{fmt(p)}·{fmt(annulus)}", p * annulus / 1e3, "кН") * 1e3
    result.step("Толкание в тоннах-силы", "F₁/g", "", push / units.G / 1e3, "тс")
    result.step("Тяга в тоннах-силы", "F₂/g", "", pull / units.G / 1e3, "тс")
    load = v["kd"] * v["load"]
    if load > 0:
        need = result.step("Давление под нагрузку (толкание)", "p = kд·F/(π·D²/4)", f"{fmt(load)}/{fmt(piston)}",
                           load / piston, "МПа")
        result.step("Давление под нагрузку (тяга)", "p = kд·F/(π·(D² − d²)/4)", f"{fmt(load)}/{fmt(annulus)}",
                    load / annulus, "МПа")
        result.check("Усилие толкания против нагрузки", load / 1e3, push / 1e3, "кН", 1.0, "F₁ при заданном давлении")
        result.notes.append(f"Для нагрузки нужно {fmt(need)} МПа ({fmt(need * 10)} бар) на толкание")
    rod_force = max(push, load)
    rod_area = math.pi * d * d / 4
    sigma = result.step("Напряжение в штоке", "σ = F/(π·d²/4)", f"{fmt(rod_force)}/{fmt(rod_area)}",
                        rod_force / rod_area, "МПа")
    result.check("Прочность штока на сжатие", sigma, rod.yield_, "МПа", v["n_required"], "σт штока")
    if v["length"] > 0:
        mu = stability.MU[v["scheme"]]
        inertia = math.pi * d ** 4 / 64
        critical = result.step("Критическая сила штока", "Pcr = π²·E·I/(μ·L)²",
                               f"π²·{fmt(rod.elastic)}·{fmt(inertia)}/({fmt(mu)}·{fmt(v['length'])})²",
                               math.pi ** 2 * rod.elastic * inertia / (mu * v["length"]) ** 2 / 1e3, "кН") * 1e3
        lam = mu * v["length"] / (d / 4)
        result.step("Гибкость штока", "λ = μ·L/(d/4)", f"{fmt(mu)}·{fmt(v['length'])}/({fmt(d)}/4)", lam, "")
        result.check("Устойчивость штока при полном вылете", rod_force / 1e3, critical / 1e3, "кН", v["n_buckling"],
                     "Pcr по Эйлеру")
        if lam < 100:
            result.notes.append(f"Гибкость штока {fmt(lam)} меньше 100: формула Эйлера завышает критическую силу — "
                                "решает проверка на сжатие и запас по устойчивости берите с запасом")
        result.notes.append("Устойчивость посчитана по одному штоку на всей длине — гильза жёстче, на деле запас больше")
    else:
        result.notes.append("Длина между пальцами не задана — шток на продольный изгиб не проверен")
    result.notes.append(rod.describe())

    if suggest and result.status in ("fail", "low"):
        if load > push:
            for bore in BORES:
                if bore > D and math.pi * bore * bore / 4 * p >= load:
                    result.suggestions.append(f"поршень ⌀{bore} — толкает {fmt(p * math.pi * bore * bore / 4 / 1e3)} кН")
                    break
            result.suggestions.append(f"давление {fmt(load / piston)} МПа на этом поршне")
        rod_checks = [c for c in result.checks if "штока" in c.title]
        if rod_checks and min(c.margin / c.required for c in rod_checks) < 1:
            for size in RODS:
                if size <= d or size >= D:
                    continue
                r = _calculate({**v, "rod": float(size)}, [], suggest=False)
                checks = [c for c in r.checks if "штока" in c.title]
                if min(c.margin / c.required for c in checks) >= 1:
                    result.suggestions.append(f"шток ⌀{size} — запас устойчивости "
                                              f"{fmt(min(c.margin for c in checks if 'Устойчивость' in c.title), 2) if any('Устойчивость' in c.title for c in checks) else fmt(r.margin, 2)}")
                    break
            else:
                result.suggestions.append(f"штоки до ⌀{max(r for r in RODS if r < D)} не дают запаса — уменьшите вылет, "
                                          "поставьте цилиндр с большим поршнем или направляющую штока")
    return result
