"""Welded joints: fillet welds (shear on the throat) and butt welds.

Machine building (Решетов, «Детали машин»; Иванов): the stress against a
share of the yield point of the base metal —
    fillet weld   τ = N/(β·k·lw),  β = 0,7;  limit 0,6·σт (Э42, Э46) or 0,65·σт (Э50, Св-08Г2С)
    butt weld     σ = N/(t·lw);  limit in tension 0,9·σт (manual, no inspection) or σт,
                  in compression σт, in shear 0,6·σт
and the margin limit / stress against the required [n] (1,5 by default).

СП 16.13330.2017, п. 14.1: a fillet weld on the weld metal and on the fusion
boundary — N/(βf·kf·lw) ≤ Rwf·γc and N/(βz·kf·lw) ≤ Rwz·γc, Rwz = 0,45·Run,
βf = 0,7, βz = 1,0 (manual welding, semi-automatic with wire under 1,4 mm);
a butt weld — N/(t·lw) ≤ Rwy·γc, Rwy = Ry with physical inspection, 0,85·Ry
without (tension), Rws = 0,58·Ry in shear; Ry = σт/γm.  The loads are the
design ones: the load factors are in them, the required margin is 1.

A moment in the plane of the weld adds τM = M/W, W = β·k·lw²/6 per weld
(t·lw²/6 for a butt weld); the stresses are added as √(τN² + τM²).
The calculated length lw = l − 2k (l − 2t for a butt weld): the craters at
the ends; a weld all round or with run-off tabs counts in full.
"""
from __future__ import annotations

import math

from . import strength as strength_mod
from . import units
from .core import CalcError, Result, fmt
from .fields import BASIS_MACHINE, BOOL, CHOICE, MATERIAL, Field, basis_field, common_fields, read

KIND = "weld"
TITLE = "Сварной шов"
FILLET, BUTT = "угловой", "стыковой"
TENSION, COMPRESSION, SHEAR = "растяжение", "сжатие", "срез"
ELECTRODES = ("ручная, Э42", "ручная, Э46", "ручная, Э50", "полуавтомат, Св-08Г2С")
# СП 16, табл. Г.2: Rwf of the weld metal, MPa
RWF = {"ручная, Э42": 180.0, "ручная, Э46": 200.0, "ручная, Э50": 215.0, "полуавтомат, Св-08Г2С": 215.0}
# machine building: the limit of a fillet weld in shear as a share of σт of the base metal
FILLET_SHARE = {"ручная, Э42": 0.6, "ручная, Э46": 0.6, "ручная, Э50": 0.65, "полуавтомат, Св-08Г2С": 0.65}
BETA_F, BETA_Z = 0.7, 1.0
# СП 16, табл. 38: the least leg by the thicker part (tee and corner joints, Ryn up to 285 MPa)
KF_MIN = ((5, 4), (10, 5), (16, 6), (22, 7), (32, 8), (40, 9), (80, 10))

FIELDS = [
    Field("joint", "Тип шва", CHOICE, FILLET, (FILLET, BUTT), "угловой (тавровое, нахлёсточное, угловое) или стыковой"),
    Field("force", "Сила N", units.FORCE, hint="сила, которую передают швы, кН (можно кгс, тс)"),
    Field("moment", "Момент M в плоскости шва", units.MOMENT, 0.0, hint="изгибающий момент на шов, Н·м"),
    Field("leg", "Катет k", units.LENGTH, 0.0, hint="катет углового шва, мм"),
    Field("thickness", "Толщина t (стыковой — тоньшей детали)", units.LENGTH, 0.0,
          hint="для стыкового шва — толщина тоньшей детали; для углового — тоньшей из свариваемых, мм"),
    Field("thickness_max", "Толщина толстой детали", units.LENGTH, 0.0, hint="для минимального катета по СП 16, мм",
          advanced=True),
    Field("length", "Длина шва l", units.LENGTH, hint="длина одного шва, мм"),
    Field("count", "Число швов", units.NUMBER, 1.0, hint="одинаковых швов, которые делят силу (два фланговых — 2)"),
    Field("full_length", "Шов кругом или с выводными планками", BOOL, False,
          hint="да — считать всю длину; нет — минус кратеры по концам"),
    Field("load", "Стыковой шов работает на", CHOICE, TENSION, (TENSION, COMPRESSION, SHEAR)),
    Field("electrode", "Сварка", CHOICE, ELECTRODES[1], ELECTRODES),
    Field("inspection", "Физический контроль шва", BOOL, False, hint="рентген или УЗК — для стыкового шва на растяжение",
          advanced=True),
    Field("material", "Металл деталей", MATERIAL, "Ст3", hint="марка стали свариваемых деталей",
          material_kinds=("sheet", "checker")),
    basis_field(),
    *common_fields(1.5),
    Field("gamma_c", "γc (СП 16)", units.NUMBER, 1.0, advanced=True),
    Field("gamma_m", "γm (СП 16)", units.NUMBER, 1.05, hint="коэффициент надёжности по материалу, СП 16 табл. 3",
          advanced=True),
]


def kf_min(thickness_max: float) -> float:
    for t, k in KF_MIN:
        if thickness_max <= t:
            return min(k, thickness_max)
    return KF_MIN[-1][1]


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    fillet = values.values["joint"] == FILLET
    skip = ("load", "inspection") if fillet else ("leg", "thickness_max")
    inputs = values.inputs("joint", "electrode", "material", "basis", "kd", skip=skip)
    return _calculate(values.values, inputs, suggest=True)


def _calculate(v: dict, inputs: list, suggest: bool) -> Result:
    machine = v["basis"] == BASIS_MACHINE
    fillet = v["joint"] == FILLET
    count = max(1, round(v["count"]))
    kd = v["kd"]
    n_req = v["n_required"] if machine else 1.0
    t = v["thickness"]
    k = v["leg"]
    if fillet and k <= 0:
        raise CalcError("для углового шва нужен катет k")
    if not fillet and t <= 0:
        raise CalcError("для стыкового шва нужна толщина t")
    s = strength_mod.strength(v["material"], max(t, v["thickness_max"]) or None, ("sheet", "checker", "solid"))
    result = Result(KIND, f"{TITLE}: {v['joint']}",
                    "машиностроение — запас по пределу текучести основного металла" if machine
                    else "СП 16.13330.2017, п. 14.1 — расчётные сопротивления сварных швов")
    result.inputs = inputs
    N = v["force"] * kd
    M = v["moment"] * kd
    if kd != 1:
        result.step("Расчётная сила", "N·kд", f"{fmt(v['force'] / 1e3)}·{fmt(kd)}", N / 1e3, "кН")
    crater = 0.0 if v["full_length"] else 2 * (k if fillet else t)
    lw = result.step("Расчётная длина шва", "lw = l" + ("" if not crater else (" − 2k" if fillet else " − 2t")),
                     f"{fmt(v['length'])}" + (f" − {fmt(crater)}" if crater else ""), v["length"] - crater, "мм")
    if lw <= 0:
        raise CalcError(f"шов {fmt(v['length'])} мм слишком короткий для катета {fmt(k)} мм")
    if fillet:
        _fillet(result, v, s, N, M, lw, count, machine, n_req)
    else:
        _butt(result, v, s, N, M, lw, count, machine, n_req)
    result.notes.append(s.describe())
    if suggest:
        _suggest(result, v, n_req)
    return result


def _fillet(result: Result, v: dict, s, N: float, M: float, lw: float, count: int, machine: bool, n_req: float) -> None:
    k = v["leg"]
    electrode = v["electrode"]
    if machine:
        area = result.step("Площадь сечения швов по катету", "Aw = n·0,7·k·lw",
                           f"{count}·0,7·{fmt(k)}·{fmt(lw)}", count * 0.7 * k * lw, "мм²")
        tau_n = result.step("Напряжение от силы", "τN = N/Aw", f"{fmt(N)}/{fmt(area)}", N / area, "МПа")
        tau = tau_n
        if M:
            w = result.step("Момент сопротивления швов", "Ww = n·0,7·k·lw²/6",
                            f"{count}·0,7·{fmt(k)}·{fmt(lw)}²/6", count * 0.7 * k * lw ** 2 / 6, "мм³")
            tau_m = result.step("Напряжение от момента", "τM = M/Ww", f"{fmt(M)}/{fmt(w)}", M / w, "МПа")
            tau = result.step("Суммарное напряжение", "τ = √(τN² + τM²)", f"√({fmt(tau_n)}² + {fmt(tau_m)}²)",
                              math.hypot(tau_n, tau_m), "МПа")
        share = FILLET_SHARE[electrode]
        limit = result.step("Предельное напряжение шва на срез", f"{fmt(share)}·σт",
                            f"{fmt(share)}·{fmt(s.yield_)}", share * s.yield_, "МПа")
        result.check("Прочность шва на срез", tau, limit, "МПа", n_req, f"{fmt(share)}·σт ({electrode})")
    else:
        gamma_c = v["gamma_c"]
        rwf = RWF[electrode]
        rwz = result.step("Rwz по границе сплавления", "Rwz = 0,45·Run", f"0,45·{fmt(s.ultimate)}",
                          0.45 * s.ultimate, "МПа")
        for title, beta, r, name in (("по металлу шва", BETA_F, rwf, "Rwf"), ("по границе сплавления", BETA_Z, rwz, "Rwz")):
            tau = result.step(f"Напряжение {title} от силы", f"N/(n·β·kf·lw)",
                              f"{fmt(N)}/({count}·{fmt(beta)}·{fmt(k)}·{fmt(lw)})", N / (count * beta * k * lw), "МПа")
            if M:
                w = count * beta * k * lw ** 2 / 6
                tau_m = result.step(f"Напряжение {title} от момента", "M/(n·β·kf·lw²/6)", f"{fmt(M)}/{fmt(w)}", M / w, "МПа")
                tau = result.step(f"Суммарное {title}", "√(τN² + τM²)", "", math.hypot(tau, tau_m), "МПа")
            result.check(f"Прочность шва {title}", tau, r * gamma_c, "МПа", 1.0,
                         f"{name}·γc = {fmt(r)}·{fmt(gamma_c)}")
    thickness = v["thickness"]
    if thickness and k > 1.2 * thickness:
        result.notes.append(f"Катет {fmt(k)} мм больше 1,2·t = {fmt(1.2 * thickness)} мм тонкой детали "
                            "(СП 16, п. 14.1.7): прожог кромки")
    thick = v["thickness_max"] or thickness
    if thick:
        least = kf_min(thick)
        if k < least:
            result.notes.append(f"Катет {fmt(k)} мм меньше минимального {fmt(least)} мм для детали {fmt(thick)} мм "
                                "(СП 16, табл. 38): шов остынет слишком быстро")
    if lw < max(4 * k, 40):
        result.notes.append(f"Расчётная длина {fmt(lw)} мм меньше 4·k и 40 мм — такой шов по СП 16 в расчёт не берут")
    if lw > 85 * BETA_F * k and not v["full_length"]:
        result.notes.append(f"Фланговый шов длиннее 85·βf·kf = {fmt(85 * BETA_F * k)} мм работает неравномерно (СП 16, п. 14.1.7)")


def _butt(result: Result, v: dict, s, N: float, M: float, lw: float, count: int, machine: bool, n_req: float) -> None:
    t = v["thickness"]
    load = v["load"]
    area = result.step("Площадь сечения шва", "Aw = n·t·lw", f"{count}·{fmt(t)}·{fmt(lw)}", count * t * lw, "мм²")
    sigma = result.step("Напряжение от силы", "σN = N/Aw", f"{fmt(N)}/{fmt(area)}", N / area, "МПа")
    if M:
        w = result.step("Момент сопротивления шва", "Ww = n·t·lw²/6", f"{count}·{fmt(t)}·{fmt(lw)}²/6",
                        count * t * lw ** 2 / 6, "мм³")
        sigma_m = result.step("Напряжение от момента", "σM = M/Ww", f"{fmt(M)}/{fmt(w)}", M / w, "МПа")
        if load == SHEAR:
            sigma = result.step("Приведённое напряжение", "√(σM² + 3·τN²)", "", math.sqrt(sigma_m ** 2 + 3 * sigma ** 2), "МПа")
        else:
            sigma = result.step("Суммарное напряжение", "σ = σN + σM", f"{fmt(sigma)} + {fmt(sigma_m)}", sigma + sigma_m, "МПа")
    quality = v["inspection"] or v["electrode"] in ("ручная, Э50", "полуавтомат, Св-08Г2С")
    if machine:
        share = {TENSION: 1.0 if quality else 0.9, COMPRESSION: 1.0, SHEAR: 0.6}[load]
        if M and load == SHEAR:
            share = 1.0
        limit = result.step("Предельное напряжение шва", f"{fmt(share)}·σт", f"{fmt(share)}·{fmt(s.yield_)}",
                            share * s.yield_, "МПа")
        result.check(f"Прочность стыкового шва ({load})", sigma, limit, "МПа", n_req, f"{fmt(share)}·σт")
    else:
        ry = result.step("Ry основного металла", "Ry = σт/γm", f"{fmt(s.yield_)}/{fmt(v['gamma_m'])}",
                         s.yield_ / v["gamma_m"], "МПа")
        if load == SHEAR and not M:
            r, name = 0.58 * ry, "Rws = 0,58·Ry"
        elif load == TENSION and not v["inspection"]:
            r, name = 0.85 * ry, "Rwy = 0,85·Ry"
        else:
            r, name = ry, "Rwy = Ry"
        result.step("Расчётное сопротивление шва", name, "", r, "МПа")
        result.check(f"Прочность стыкового шва ({load})", sigma, r * v["gamma_c"], "МПа", 1.0, f"{name.split(' =')[0]}·γc")


def _rerun(v: dict, **changes) -> Result:
    return _calculate({**v, **changes}, [], suggest=False)


def _suggest(result: Result, v: dict, n_req: float) -> None:
    margin = result.margin
    if result.governing is None:
        return
    fillet = v["joint"] == FILLET
    if margin < n_req:
        if fillet:
            cap = 1.2 * v["thickness"] if v["thickness"] else 3 * v["leg"] + 10
            k = math.floor(v["leg"]) + 1
            while k <= cap:
                try:
                    better = _rerun(v, leg=float(k))
                except CalcError:
                    break
                if better.margin >= n_req:
                    result.suggestions.append(f"катет {k} мм даёт запас {fmt(better.margin, 2)}")
                    break
                k += 1
        length = math.ceil(v["length"] / 10) * 10 + 10
        while length <= v["length"] * 4:
            better = _rerun(v, length=float(length))
            if better.margin >= n_req:
                result.suggestions.append(f"шов длиной {length} мм даёт запас {fmt(better.margin, 2)}")
                break
            length += 10
        if fillet and v["electrode"] in ("ручная, Э42", "ручная, Э46"):
            better = _rerun(v, electrode="полуавтомат, Св-08Г2С")
            if better.margin > margin * 1.02:
                result.suggestions.append(f"полуавтомат Св-08Г2С — запас {fmt(better.margin, 2)}")
    elif margin > 3 * n_req and fillet and v["leg"] > 3:
        k = math.ceil(v["leg"]) - 1
        best = None
        while k >= 3:
            try:
                smaller = _rerun(v, leg=float(k))
            except CalcError:
                break
            if smaller.margin < n_req * 1.2:
                break
            best = (k, smaller.margin)
            k -= 1
        if best:
            result.suggestions.append(f"хватит катета {best[0]} мм (запас {fmt(best[1], 2)})")
