"""Shafts and axles: bending with torsion, fatigue, the parallel key.

Static strength (the fourth strength theory), with the peak load factor kд:
    W = π·d³/32·(1 − α⁴), Wp = 2·W, α = d0/d
    σ = kд·M/W,  τ = kд·T/Wp,  σэкв = √(σ² + 3·τ²) = kд·√(M² + 0,75·T²)/W ≤ σт/[n]
Fatigue (Иосилевич, «Детали машин»; Дунаев, Леликов), with the loads as given:
    σ−1 ≈ 0,43·σв, τ−1 ≈ 0,58·σ−1, ψσ ≈ 0,02 + 2·10⁻⁴·σв, ψτ ≈ 0,5·ψσ
    a rotating shaft: bending in a symmetric cycle (σa = σ, σm = 0); a fixed axle: a pulsating one
    (σa = σm = σ/2); torsion pulsating (τa = τm = τ/2)
    sσ = σ−1/(Kσ/(εσ·β)·σa + ψσ·σm), sτ the same, s = sσ·sτ/√(sσ² + sτ²) ≥ [s] = 1,5…2,5
    Kσ, Kτ of the stress raiser by σв, εσ, ετ by the diameter, β by the surface — the handbook tables
Parallel key ГОСТ 23360-78 (b×h and t1 by the diameter), working length lp = l − b:
    σсм = 2·kд·T/(d·(h − t1)·lp) against 0,6·σт, τср = 2·kд·T/(d·b·lp) against 0,35·σт of the key
    (the allowable [σсм] ≈ 0,4·σт and [τср] ≈ 0,23·σт at [n] = 1,5)
"""
from __future__ import annotations

import math

from . import strength as strength_mod
from . import units
from .core import CalcError, Result, fmt
from .fields import BOOL, CHOICE, MATERIAL, Field, common_fields, read

KIND = "shaft"
TITLE = "Вал"
SMOOTH, KEYWAY, FILLET, PRESS_FIT, HOLE = ("нет (гладкий)", "шпоночный паз", "галтель (r/d ≈ 0,05)",
                                           "посадка с натягом", "поперечное отверстие")
# raiser: rows (σв, Kσ, Kτ); for a press fit — Kσ/εσ and Kτ/ετ directly (scale included)
RAISERS = {
    SMOOTH: ((500, 1.0, 1.0), (1200, 1.0, 1.0)),
    KEYWAY: ((500, 1.8, 1.4), (700, 2.0, 1.7), (900, 2.2, 2.05), (1200, 2.65, 2.4)),
    FILLET: ((500, 1.9, 1.45), (700, 2.0, 1.55), (900, 2.1, 1.6), (1200, 2.2, 1.7)),
    PRESS_FIT: ((500, 2.5, 1.9), (700, 3.0, 2.2), (900, 3.5, 2.5), (1200, 4.25, 3.0)),
    HOLE: ((500, 1.9, 1.75), (700, 2.0, 1.8), (900, 2.1, 1.85), (1200, 2.2, 1.9)),
}
# d mm: (εσ carbon steel, ε alloy steel; ετ — the alloy column for all)
SCALE = ((20, 0.92, 0.83), (30, 0.88, 0.77), (40, 0.85, 0.73), (50, 0.82, 0.70), (70, 0.76, 0.65),
         (100, 0.70, 0.59), (200, 0.62, 0.52))
SURFACES = {"шлифование": 1.0, "чистовое точение": 0.9, "черновое точение": 0.8}
# ГОСТ 23360-78: d up to: (b, h, t1)
KEYS = ((8, (2, 2, 1.2)), (10, (3, 3, 1.8)), (12, (4, 4, 2.5)), (17, (5, 5, 3.0)), (22, (6, 6, 3.5)),
        (30, (8, 7, 4.0)), (38, (10, 8, 5.0)), (44, (12, 8, 5.0)), (50, (14, 9, 5.5)), (58, (16, 10, 6.0)),
        (65, (18, 11, 7.0)), (75, (20, 12, 7.5)), (85, (22, 14, 9.0)), (95, (25, 14, 9.0)), (110, (28, 16, 10.0)),
        (130, (32, 18, 11.0)), (150, (36, 20, 12.0)), (170, (40, 22, 13.0)), (200, (45, 25, 15.0)))
KEY_LENGTHS = (6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40, 45, 50, 56, 63, 70, 80, 90, 100, 110, 125, 140,
               160, 180, 200, 220, 250)
DIAMETERS = (16, 18, 20, 22, 24, 25, 26, 28, 30, 32, 34, 35, 36, 38, 40, 42, 45, 48, 50, 53, 55, 56, 60, 63, 65,
             67, 70, 71, 75, 80, 85, 90, 95, 100, 105, 110, 120, 125, 130, 140, 150, 160, 170, 180, 190, 200)

FIELDS = [
    Field("diameter", "Диаметр d", units.LENGTH),
    Field("torque", "Крутящий момент T", units.MOMENT, 0.0),
    Field("moment", "Изгибающий момент M", units.MOMENT, 0.0, hint="в опасном сечении; можно посчитать на вкладке «Балки»"),
    Field("bore", "Внутренний диаметр d0 (полый вал)", units.LENGTH, 0.0, advanced=True),
    Field("material", "Материал", MATERIAL, "Сталь 45", material_kinds=("solid",)),
    Field("rotating", "Вал вращается", BOOL, True, hint="нет — неподвижная ось (ось прицепа): изгиб отнулевой"),
    Field("raiser", "Концентратор в сечении", CHOICE, KEYWAY, tuple(RAISERS)),
    Field("surface", "Обработка поверхности", CHOICE, "чистовое точение", tuple(SURFACES), advanced=True),
    Field("key", "Проверить шпонку", BOOL, False),
    Field("key_length", "Длина шпонки l", units.LENGTH, 0.0, hint="полная длина шпонки исполнения 1, мм"),
    Field("hub_material", "Материал ступицы", MATERIAL, "", hint="пусто — как вал", material_kinds=("solid",),
          advanced=True),
    *common_fields(1.5),
    Field("s_required", "Нужный запас выносливости [s]", units.NUMBER, 1.5, hint="1,5–2,5", advanced=True),
]


def _table(rows, x: float, col: int) -> float:
    """Linear in a table by its first column, the end rows outside."""
    if x <= rows[0][0]:
        return rows[0][col]
    for a, b in zip(rows, rows[1:]):
        if x <= b[0]:
            return a[col] + (b[col] - a[col]) * (x - a[0]) / (b[0] - a[0])
    return rows[-1][col]


def key_for(d: float) -> tuple[float, float, float]:
    for limit, sizes in KEYS:
        if d <= limit:
            return sizes
    raise CalcError(f"шпонки для вала {fmt(d)} мм нет в таблице ГОСТ 23360 (до 200 мм)")


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    inputs = values.inputs("diameter", "material", "raiser", "rotating", "kd")
    return _calculate(v, inputs, None, suggest=True)


def _calculate(v: dict, inputs: list, material, suggest: bool) -> Result:
    d, d0 = v["diameter"], v["bore"]
    if d <= 0 or d0 >= d:
        raise CalcError("диаметр должен быть больше нуля и больше внутреннего")
    T, M, kd = v["torque"], v["moment"], v["kd"]
    if not (T or M):
        raise CalcError("нужен крутящий или изгибающий момент")
    s = strength_mod.strength(material or v["material"], d, ("solid",))
    n_req = v["n_required"]
    result = Result(KIND, f"{TITLE} Ø{fmt(d)}" + (f"/{fmt(d0)}" if d0 else "") + f", {s.name}",
                    "машиностроение — статическая прочность по пределу текучести и выносливость")
    result.inputs = inputs
    alpha = d0 / d
    w = result.step("Момент сопротивления изгибу", "W = π·d³/32" + ("·(1 − α⁴)" if d0 else ""),
                    f"π·{fmt(d)}³/32" + (f"·(1 − {fmt(alpha)}⁴)" if d0 else ""), math.pi * d ** 3 / 32 * (1 - alpha ** 4),
                    "мм³")
    sigma = result.step("Напряжение изгиба", "σ = kд·M/W", f"{fmt(kd)}·{fmt(M)}/{fmt(w)}", kd * M / w, "МПа")
    s_eq = sigma
    if T:
        tau = result.step("Напряжение кручения", "τ = kд·T/Wp, Wp = 2·W", f"{fmt(kd)}·{fmt(T)}/{fmt(2 * w)}",
                          kd * T / (2 * w), "МПа")
        s_eq = result.step("Эквивалентное напряжение", "σэкв = √(σ² + 3·τ²)", f"√({fmt(sigma)}² + 3·{fmt(tau)}²)",
                           math.sqrt(sigma ** 2 + 3 * tau ** 2), "МПа")
    result.check("Статическая прочность", s_eq, s.yield_, "МПа", n_req, "σт")
    _fatigue(result, v, s, M / w, T / (2 * w))
    if v["key"]:
        _key(result, v, s, T * kd, n_req)
    result.notes.append(s.describe())
    if suggest:
        _suggest(result, v, s)
    return result


def _fatigue(result: Result, v: dict, s, sigma: float, tau: float) -> None:
    su = s.ultimate
    d = v["diameter"]
    s_1 = result.step("Предел выносливости при изгибе", "σ−1 ≈ 0,43·σв", f"0,43·{fmt(su)}", 0.43 * su, "МПа")
    t_1 = result.step("Предел выносливости при кручении", "τ−1 ≈ 0,58·σ−1", f"0,58·{fmt(s_1)}", 0.58 * s_1, "МПа")
    psi_s = 0.02 + 2e-4 * su
    psi_t = 0.5 * psi_s
    raiser = v["raiser"]
    rows = RAISERS[raiser]
    k_s, k_t = _table(rows, su, 1), _table(rows, su, 2)
    beta = SURFACES[v["surface"]]
    if raiser == PRESS_FIT:
        eps_s = eps_t = 1.0
    else:
        eps_s = _table(SCALE, d, 2 if s.alloyed else 1)
        eps_t = _table(SCALE, d, 2)
    ks = result.step(f"Коэффициент снижения выносливости, изгиб ({raiser})", "Kσ/(εσ·β)",
                     f"{fmt(k_s)}/({fmt(eps_s)}·{fmt(beta)})", k_s / (eps_s * beta), "")
    kt = result.step("То же, кручение", "Kτ/(ετ·β)", f"{fmt(k_t)}/({fmt(eps_t)}·{fmt(beta)})", k_t / (eps_t * beta), "")
    if v["rotating"]:
        sa, sm = sigma, 0.0
    else:
        sa = sm = sigma / 2
    ta = tm = tau / 2
    s_sigma = s_1 / (ks * sa + psi_s * sm) if sigma else math.inf
    s_tau = t_1 / (kt * ta + psi_t * tm) if tau else math.inf
    if sigma:
        result.step("Запас выносливости по изгибу", "sσ = σ−1/(Kσ/(εσ·β)·σa + ψσ·σm)",
                    f"{fmt(s_1)}/({fmt(ks)}·{fmt(sa)} + {fmt(psi_s)}·{fmt(sm)})", s_sigma, "")
    if tau:
        result.step("Запас выносливости по кручению", "sτ = τ−1/(Kτ/(ετ·β)·τa + ψτ·τm)",
                    f"{fmt(t_1)}/({fmt(kt)}·{fmt(ta)} + {fmt(psi_t)}·{fmt(tm)})", s_tau, "")
    if sigma and tau:
        total = result.step("Общий запас выносливости", "s = sσ·sτ/√(sσ² + sτ²)", "",
                            s_sigma * s_tau / math.hypot(s_sigma, s_tau), "")
    else:
        total = min(s_sigma, s_tau)
    result.check("Выносливость (усталость)", 0, 0, "", v["s_required"], "s", margin=total)
    result.notes.append("Выносливость — по номинальным нагрузкам, без kд; коэффициенты Kσ, ε, β — средние "
                        "по справочникам: для ответственного вала сверьте с вашим")


def _key(result: Result, v: dict, s, T: float, n_req: float) -> None:
    d = v["diameter"]
    b, h, t1 = key_for(d)
    length = v["key_length"]
    if length <= b:
        raise CalcError(f"шпонка {fmt(b)}×{fmt(h)}: нужна длина больше {fmt(b)} мм")
    lp = result.step(f"Рабочая длина шпонки {fmt(b)}×{fmt(h)} (ГОСТ 23360, t1 = {fmt(t1)})", "lp = l − b",
                     f"{fmt(length)} − {fmt(b)}", length - b, "мм")
    crush = result.step("Напряжение смятия шпонки", "σсм = 2·kд·T/(d·(h − t1)·lp)",
                        f"2·{fmt(T)}/({fmt(d)}·({fmt(h)} − {fmt(t1)})·{fmt(lp)})", 2 * T / (d * (h - t1) * lp), "МПа")
    hub = strength_mod.strength(v["hub_material"], d, ("solid",)) if v["hub_material"] else s
    key = strength_mod.strength("Сталь 45", None, ("solid",))
    weak = min(hub.yield_, key.yield_, s.yield_)
    result.check("Смятие шпонки", crush, 0.6 * weak, "МПа", n_req, "0,6·σт слабейшей детали")
    shear = result.step("Напряжение среза шпонки", "τср = 2·kд·T/(d·b·lp)", f"2·{fmt(T)}/({fmt(d)}·{fmt(b)}·{fmt(lp)})",
                        2 * T / (d * b * lp), "МПа")
    result.check("Срез шпонки", shear, 0.35 * key.yield_, "МПа", n_req, "0,35·σт шпонки (сталь 45)")


def _fits(r: Result) -> bool:
    return all(c.margin >= c.required for c in r.checks)


def _suggest(result: Result, v: dict, s) -> None:
    if _fits(result):
        return
    key_checks = [c for c in result.checks if "шпонк" in c.title]
    if key_checks and all(c.margin >= c.required for c in result.checks if c not in key_checks):
        b = key_for(v["diameter"])[0]
        for length in (x for x in KEY_LENGTHS if x > v["key_length"] and x - b <= 2.5 * v["diameter"]):
            r = _calculate({**v, "key_length": float(length)}, [], None, suggest=False)
            if _fits(r):
                result.suggestions.append(f"шпонка длиной {length} мм — запас {fmt(r.margin, 2)}")
                return
        result.suggestions.append("одной шпонки мало — две шпонки под 180° или шлицы")
    for d in (x for x in DIAMETERS if x > v["diameter"]):
        try:
            r = _calculate({**v, "diameter": float(d), "bore": v["bore"] * d / v["diameter"]}, [], None, suggest=False)
        except CalcError:
            continue
        if _fits(r):
            result.suggestions.append(f"Ø{d} — запас {fmt(r.margin, 2)}")
            break
    for better in strength_mod.stronger(s, v["diameter"]):
        r = _calculate(v, [], better.material, suggest=False)
        if _fits(r):
            result.suggestions.append(f"{better.name} — запас {fmt(r.margin, 2)}")
            break
