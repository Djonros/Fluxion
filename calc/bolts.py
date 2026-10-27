"""Bolted joints: shear, bearing, tension, tightening and friction.

Bolts: metric coarse thread, the stress area As by ГОСТ Р ИСО 898-1, σт and
σв by the strength class (4.6 … 12.9; 8.8 above M16 — 660/830 MPa).

Machine building (Решетов; Биргер), the load per bolt Qb = kд·Q/z:
    shear      τ = Qb/(i·A),  A = π·d²/4 (As when the thread is in the shear plane);  limit 0,6·σт of the bolt
    bearing    σсм = Qb/(d·Σt);  limit 1,2·σт of the weaker of the bolt and the plates
    tension    σ = kд·F/(z·As);  limit σт
    tightening F0 = M/(K·d), K ≈ 0,2 (dry thread, no lubrication); σэкв = 1,3·F0/As ≤ 0,8·σт;
               the torque for σэкв = 0,7·σт is offered
    friction   the shear held by friction f·F0·i·z against kд·Q, a margin of 1,3 against slipping
The limits are the ones of the allowable stresses [τ] = 0,4·σт, [σсм] = 0,8·σт at [n] = 1,5.

СП 16.13330.2017, п. 14.2 (Rbs, Rbt as in СНиП II-23-81*, табл. 58; Rbp by its formula
(0,6 + 410·Run/E)·Run for bolts of accuracy class B):
    Nbs = Rbs·Ab·ns·γb·γc,  Nbp = Rbp·d·Σt·γb·γc,  Nbt = Rbt·Abn·γc
"""
from __future__ import annotations

import math
import re

from . import strength as strength_mod
from . import units
from .core import CalcError, Result, fmt
from .fields import BASIS_MACHINE, BOOL, CHOICE, MATERIAL, TEXT, Field, basis_field, common_fields, read

KIND = "bolt"
TITLE = "Болтовое соединение"
# d: (pitch, As mm²)
THREADS = {6: (1.0, 20.1), 8: (1.25, 36.6), 10: (1.5, 58.0), 12: (1.75, 84.3), 14: (2.0, 115.0), 16: (2.0, 157.0),
           18: (2.5, 192.0), 20: (2.5, 245.0), 22: (2.5, 303.0), 24: (3.0, 353.0), 27: (3.0, 459.0), 30: (3.5, 561.0),
           36: (4.0, 817.0), 42: (4.5, 1120.0), 48: (5.0, 1470.0)}
# class: (σт, σв) MPa
CLASSES = {"4.6": (240.0, 400.0), "5.6": (300.0, 500.0), "5.8": (400.0, 500.0), "8.8": (640.0, 800.0),
           "10.9": (940.0, 1040.0), "12.9": (1100.0, 1220.0)}
# СП 16 / СНиП II-23-81*, табл. 58: class: (Rbs, Rbt) MPa
SP_RESISTANCE = {"4.6": (150.0, 170.0), "5.6": (190.0, 210.0), "5.8": (200.0, 200.0), "8.8": (320.0, 400.0),
                 "10.9": (400.0, 500.0)}
FRICTION_MARGIN = 1.3

FIELDS = [
    Field("diameter", "Болт", TEXT, hint="М16 или 16 — номинальный диаметр резьбы"),
    Field("bolt_class", "Класс прочности", CHOICE, "8.8", tuple(CLASSES)),
    Field("count", "Число болтов z", units.NUMBER, 1.0),
    Field("planes", "Плоскостей среза i", units.NUMBER, 1.0, hint="1 — внахлёст, 2 — между двумя накладками (проушина)"),
    Field("shear", "Сдвигающая сила Q (на все болты)", units.FORCE, 0.0),
    Field("tension", "Отрывающая сила F (на все болты)", units.FORCE, 0.0),
    Field("plates", "Σt — наименьшая толщина, сминаемая в одну сторону", units.LENGTH, 0.0,
          hint="внахлёст — тоньший лист; проушина — меньшее из (средняя) и (сумма крайних), мм"),
    Field("material", "Материал листов", MATERIAL, "Ст3", material_kinds=("sheet", "checker")),
    Field("thread_in_shear", "Резьба в плоскости среза", BOOL, False),
    Field("torque", "Момент затяжки", units.MOMENT, 0.0, hint="Н·м; пусто — затяжка не проверяется, но подсказывается"),
    Field("k_torque", "Коэффициент затяжки K", units.NUMBER, 0.2, hint="0,2 — сухая резьба; 0,15 — со смазкой",
          advanced=True),
    Field("friction", "Коэффициент трения в стыке f", units.NUMBER, 0.15,
          hint="0,15 — сталь без обработки, 0,35 — дробеструйная очистка", advanced=True),
    basis_field(),
    *common_fields(1.5),
    Field("gamma_b", "γb (СП 16)", units.NUMBER, 0.9, hint="0,9 — многоболтовое соединение класса точности B",
          advanced=True),
    Field("gamma_c", "γc (СП 16)", units.NUMBER, 1.0, advanced=True),
]


def diameter_of(text) -> int:
    match = re.search(r"(\d+)", str(text))
    if not match:
        raise CalcError(f"болт «{text}»: нужен диаметр — М16 или 16")
    d = int(match.group(1))
    if d not in THREADS:
        raise CalcError(f"болта М{d} нет в таблице; есть: {', '.join(f'М{x}' for x in THREADS)}")
    return d


def bolt_strength(bolt_class: str, d: int) -> tuple[float, float]:
    if bolt_class == "8.8" and d > 16:
        return 660.0, 830.0
    return CLASSES[bolt_class]


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    v["d"] = diameter_of(v["diameter"])
    inputs = values.inputs("diameter", "bolt_class", "count", "material", "basis", "kd")
    return _calculate(v, inputs, suggest=True)


def _calculate(v: dict, inputs: list, suggest: bool) -> Result:
    machine = v["basis"] == BASIS_MACHINE
    d = v["d"]
    z = max(1, round(v["count"]))
    planes = max(1, round(v["planes"]))
    kd = v["kd"]
    n_req = v["n_required"] if machine else 1.0
    Q, F = v["shear"] * kd, v["tension"] * kd
    if not (Q or F or v["torque"]):
        raise CalcError("нужна сдвигающая или отрывающая сила (или момент затяжки)")
    bolt_class = v["bolt_class"]
    sy, su = bolt_strength(bolt_class, d)
    pitch, As = THREADS[d]
    result = Result(KIND, f"{TITLE}: М{d} класса {bolt_class}, {z} шт.",
                    "машиностроение — запас по пределу текучести" if machine
                    else "СП 16.13330.2017, п. 14.2 — расчётные сопротивления болтов")
    result.inputs = inputs
    result.step("Площадь сечения по резьбе", "As (ГОСТ Р ИСО 898-1)", "", As, "мм²")
    plate = strength_mod.strength(v["material"], v["plates"] or None, ("sheet", "checker", "solid"))
    if machine:
        _machine(result, v, d, z, planes, Q, F, sy, As, plate, n_req)
    else:
        if bolt_class not in SP_RESISTANCE:
            raise CalcError(f"класса {bolt_class} нет в СП 16 — считайте по методике машиностроения")
        _sp16(result, v, d, z, planes, Q, F, As, plate)
    _tightening(result, v, d, z, planes, sy, As)
    result.notes.append(f"Болт {bolt_class}: σт {fmt(sy)} МПа, σв {fmt(su)} МПа; шаг резьбы {fmt(pitch)} мм")
    result.notes.append(plate.describe())
    if suggest:
        _suggest(result, v, inputs, n_req)
    return result


def _machine(result: Result, v: dict, d: int, z: int, planes: int, Q: float, F: float, sy: float, As: float,
             plate, n_req: float) -> None:
    if Q:
        qb = result.step("Сила на один болт", "Qb = kд·Q/z", f"{fmt(Q)}/{z}", Q / z, "Н")
        area = As if v["thread_in_shear"] else math.pi * d ** 2 / 4
        name = "As" if v["thread_in_shear"] else "π·d²/4"
        tau = result.step("Напряжение среза", f"τ = Qb/(i·{name})", f"{fmt(qb)}/({planes}·{fmt(area)})",
                          qb / (planes * area), "МПа")
        result.check("Срез болта", tau, 0.6 * sy, "МПа", n_req, "0,6·σт болта")
        if v["plates"]:
            sigma = result.step("Напряжение смятия", "σсм = Qb/(d·Σt)", f"{fmt(qb)}/({d}·{fmt(v['plates'])})",
                                qb / (d * v["plates"]), "МПа")
            weak = min(sy, plate.yield_)
            result.check("Смятие стенок отверстий", sigma, 1.2 * weak, "МПа", n_req,
                         f"1,2·σт ({'листа' if plate.yield_ < sy else 'болта'})")
        else:
            result.notes.append("Смятие не проверено: не задана толщина листов Σt")
    if F:
        sigma = result.step("Напряжение растяжения", "σ = kд·F/(z·As)", f"{fmt(F)}/({z}·{fmt(As)})", F / (z * As), "МПа")
        result.check("Разрыв болта", sigma, sy, "МПа", n_req, "σт болта")


def _sp16(result: Result, v: dict, d: int, z: int, planes: int, Q: float, F: float, As: float, plate) -> None:
    rbs, rbt = SP_RESISTANCE[v["bolt_class"]]
    gb, gc = v["gamma_b"], v["gamma_c"]
    if Q:
        qb = result.step("Сила на один болт", "Qb = Q/z", f"{fmt(Q)}/{z}", Q / z, "Н")
        ab = As if v["thread_in_shear"] else math.pi * d ** 2 / 4
        nbs = result.step("Несущая способность на срез", "Nbs = Rbs·Ab·ns·γb·γc",
                          f"{fmt(rbs)}·{fmt(ab)}·{planes}·{fmt(gb)}·{fmt(gc)}", rbs * ab * planes * gb * gc, "Н")
        result.check("Срез болта", qb, nbs, "Н", 1.0, "Nbs")
        if v["plates"]:
            run = plate.ultimate
            rbp = result.step("Расчётное сопротивление смятию", "Rbp = (0,6 + 410·Run/E)·Run",
                              f"(0,6 + 410·{fmt(run)}/{fmt(strength_mod.E_STEEL)})·{fmt(run)}",
                              (0.6 + 410 * run / strength_mod.E_STEEL) * run, "МПа")
            nbp = result.step("Несущая способность на смятие", "Nbp = Rbp·d·Σt·γb·γc",
                              f"{fmt(rbp)}·{d}·{fmt(v['plates'])}·{fmt(gb)}·{fmt(gc)}",
                              rbp * d * v["plates"] * gb * gc, "Н")
            result.check("Смятие стенок отверстий", qb, nbp, "Н", 1.0, "Nbp")
        else:
            result.notes.append("Смятие не проверено: не задана толщина листов Σt")
    if F:
        fb = result.step("Отрывающая сила на болт", "Fb = F/z", f"{fmt(F)}/{z}", F / z, "Н")
        nbt = result.step("Несущая способность на растяжение", "Nbt = Rbt·Abn·γc", f"{fmt(rbt)}·{fmt(As)}·{fmt(gc)}",
                          rbt * As * gc, "Н")
        result.check("Разрыв болта", fb, nbt, "Н", 1.0, "Nbt")


def _tightening(result: Result, v: dict, d: int, z: int, planes: int, sy: float, As: float) -> None:
    k = v["k_torque"]
    f0_rec = 0.7 * sy * As / 1.3
    m_rec = result.step("Рекомендуемый момент затяжки (σэкв = 0,7·σт)", "M = K·d·0,7·σт·As/1,3",
                        f"{fmt(k)}·{d}·0,7·{fmt(sy)}·{fmt(As)}/1,3", k * d * f0_rec / 1000, "Н·м")
    if not v["torque"]:
        return
    f0 = result.step("Усилие затяжки", "F0 = M/(K·d)", f"{fmt(v['torque'])}/({fmt(k)}·{d})", v["torque"] / (k * d), "Н")
    s_eq = result.step("Напряжение в болте при затяжке", "σэкв = 1,3·F0/As", f"1,3·{fmt(f0)}/{fmt(As)}",
                       1.3 * f0 / As, "МПа")
    result.check("Затяжка (σэкв ≤ 0,8·σт)", s_eq, 0.8 * sy, "МПа", 1.0, "0,8·σт")
    if v["shear"]:
        held = result.step("Сдвиг, удерживаемый трением", "Qтр = f·F0·i·z",
                           f"{fmt(v['friction'])}·{fmt(f0)}·{planes}·{z}", v["friction"] * f0 * planes * z, "Н")
        ratio = result.step("Запас по сдвигу трением", "Qтр/(kд·Q)", f"{fmt(held)}/{fmt(v['shear'] * v['kd'])}",
                            held / (v["shear"] * v["kd"]), "")
        if ratio < FRICTION_MARGIN:
            result.notes.append(f"Трения не хватает (нужно в {fmt(FRICTION_MARGIN)} раза больше сдвига): "
                                "листы сдвинутся до упора в болты, и болты работают на срез и смятие")


def _suggest(result: Result, v: dict, inputs: list, n_req: float) -> None:
    def fits(r: Result) -> bool:
        return all(c.margin >= c.required for c in r.checks)

    if fits(result):
        return
    found = []
    for d in (x for x in THREADS if x > v["d"]):
        r = _calculate({**v, "d": d}, [], suggest=False)
        if fits(r):
            found.append(f"М{d} — запас {fmt(r.margin, 2)}")
            break
    order = list(CLASSES)
    for bolt_class in order[order.index(v["bolt_class"]) + 1:]:
        if v["basis"] != BASIS_MACHINE and bolt_class not in SP_RESISTANCE:
            break
        r = _calculate({**v, "bolt_class": bolt_class}, [], suggest=False)
        if fits(r):
            found.append(f"класс {bolt_class} — запас {fmt(r.margin, 2)}")
            break
    z = round(v["count"]) + 1
    while z <= max(12, 3 * v["count"]):
        r = _calculate({**v, "count": float(z)}, [], suggest=False)
        if fits(r):
            found.append(f"{z} болтов — запас {fmt(r.margin, 2)}")
            break
        z += 1
    result.suggestions += found
