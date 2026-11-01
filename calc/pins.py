"""A pin and its lugs: the drawbar hinge, the mount of a cylinder, a lifting eye.

The pin goes through a lug (the middle plate) and, in a double-shear joint, the two
cheeks of a fork. Machine building, the margin against the yield point:

    pin shear        τ = kд·F/(m·π·d²/4)                ≤ 0,6·σт (pin)
    pin bending      σ = M/(0,1·d³); M = kд·F/2·(a + s/2 + t/4) for a fork (two shears),
                     M = kд·F·(a + t/2) for one shear (a cantilever pin)        ≤ σт (pin)
    bearing          σсм = kд·F/(d·t), in a cheek kд·F/(2·d·s)                  ≤ σт (lug)
    lug, net section σ = kд·F/((b − d)·t)                                       ≤ σт (lug)
    lug, tear-out    τ = kд·F/(2·(e − d/2)·t)                                   ≤ 0,6·σт (lug)

t — the lug, s — a cheek of the fork, a — the gap between them, b — the width of
the lug across the hole, e — from the centre of the hole to the edge along the force.
The cheeks are checked with half the force. The hole is taken equal to the pin:
a clearance adds to the bearing pressure, which is noted.
"""
from __future__ import annotations

import math

from . import units
from . import strength as strength_mod
from .core import CalcError, Result, fmt
from .fields import CHOICE, MATERIAL, Field, common_fields, read

KIND = "pin"
TITLE = "Палец и проушины"
DOUBLE, SINGLE = "двухсрезный (вилка)", "односрезный"
PIN_DIAMETERS = (8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 70, 80, 90, 100, 110, 120)

FIELDS = [
    Field("force", "Сила на палец F", units.FORCE, hint="вся сила, которую передаёт шарнир"),
    Field("diameter", "Диаметр пальца d", units.LENGTH),
    Field("scheme", "Схема", CHOICE, DOUBLE, (DOUBLE, SINGLE),
          hint="двухсрезный — палец в вилке из двух щёк; односрезный — консольный палец"),
    Field("lug", "Толщина проушины t", units.LENGTH, hint="средняя проушина, через которую идёт палец"),
    Field("cheek", "Толщина щеки вилки s", units.LENGTH, 0.0, hint="каждой из двух щёк; для односрезного — опоры"),
    Field("gap", "Зазор между проушиной и щекой a", units.LENGTH, 0.0),
    Field("width", "Ширина проушины b", units.LENGTH, 0.0, hint="поперёк силы, через центр отверстия; 0 — 2,5·d"),
    Field("edge", "От центра отверстия до края e", units.LENGTH, 0.0, hint="вдоль силы; 0 — b/2"),
    Field("pin_material", "Материал пальца", MATERIAL, "40Х"),
    Field("lug_material", "Материал проушин", MATERIAL, "Ст3"),
    *common_fields(1.5),
]


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    inputs = values.inputs("scheme", "pin_material", "lug_material", "kd")
    return _calculate(v, inputs, suggest=True)


def _calculate(v: dict, inputs: list, suggest: bool) -> Result:
    F, d, t = v["force"], v["diameter"], v["lug"]
    if F <= 0 or d <= 0 or t <= 0:
        raise CalcError("нужны сила, диаметр пальца и толщина проушины больше нуля")
    double = v["scheme"] == DOUBLE
    s = v["cheek"] if v["cheek"] > 0 else (t / 2 if double else t)
    b = v["width"] if v["width"] > 0 else 2.5 * d
    e = v["edge"] if v["edge"] > 0 else b / 2
    if b <= d:
        raise CalcError(f"ширина проушины {fmt(b)} мм не больше отверстия {fmt(d)} мм")
    if e <= d / 2:
        raise CalcError(f"от центра отверстия до края {fmt(e)} мм — не больше радиуса отверстия")
    pin = strength_mod.strength(v["pin_material"], d, ("solid",))
    lug = strength_mod.strength(v["lug_material"], t, ("sheet", "solid"))
    kd, n_req = v["kd"], v["n_required"]
    shears = 2 if double else 1
    result = Result(KIND, f"{TITLE}: палец ⌀{fmt(d)} мм, {v['scheme']}, проушина {fmt(t)} мм",
                    "машиностроение: запас по пределу текучести (срез 0,6·σт)")
    result.inputs = inputs
    Fd = result.step("Расчётная сила", "kд·F", f"{fmt(kd)}·{fmt(F / 1e3)}", kd * F / 1e3, "кН") * 1e3

    area = math.pi * d * d / 4
    tau = result.step("Срез пальца", "τ = kд·F/(m·π·d²/4)", f"{fmt(Fd)}/({shears}·{fmt(area)})", Fd / (shears * area), "МПа")
    result.check("Срез пальца", tau, 0.6 * pin.yield_, "МПа", n_req, "0,6·σт пальца")
    a = v["gap"]
    if double:
        M = result.step("Изгибающий момент в пальце", "M = kд·F/2·(a + s/2 + t/4)",
                        f"{fmt(Fd)}/2·({fmt(a)} + {fmt(s)}/2 + {fmt(t)}/4)", Fd / 2 * (a + s / 2 + t / 4) / 1e3, "Н·м") * 1e3
    else:
        M = result.step("Изгибающий момент в пальце", "M = kд·F·(a + t/2)", f"{fmt(Fd)}·({fmt(a)} + {fmt(t)}/2)",
                        Fd * (a + t / 2) / 1e3, "Н·м") * 1e3
    sigma_b = result.step("Изгиб пальца", "σ = M/(0,1·d³)", f"{fmt(M)}/(0,1·{fmt(d)}³)", M / (0.1 * d ** 3), "МПа")
    result.check("Изгиб пальца", sigma_b, pin.yield_, "МПа", n_req, "σт пальца")

    bearing = result.step("Смятие в проушине", "σсм = kд·F/(d·t)", f"{fmt(Fd)}/({fmt(d)}·{fmt(t)})", Fd / (d * t), "МПа")
    result.check("Смятие в проушине", bearing, lug.yield_, "МПа", n_req, "σт проушины")
    net = result.step("Разрыв проушины у отверстия", "σ = kд·F/((b − d)·t)", f"{fmt(Fd)}/(({fmt(b)} − {fmt(d)})·{fmt(t)})",
                      Fd / ((b - d) * t), "МПа")
    result.check("Разрыв проушины по сечению у отверстия", net, lug.yield_, "МПа", n_req, "σт проушины")
    tear = result.step("Срез перемычки за отверстием", "τ = kд·F/(2·(e − d/2)·t)",
                       f"{fmt(Fd)}/(2·({fmt(e)} − {fmt(d)}/2)·{fmt(t)})", Fd / (2 * (e - d / 2) * t), "МПа")
    result.check("Срез перемычки за отверстием", tear, 0.6 * lug.yield_, "МПа", n_req, "0,6·σт проушины")
    if double:
        cheek_bearing = result.step("Смятие в щеке вилки", "σсм = kд·F/(2·d·s)", f"{fmt(Fd)}/(2·{fmt(d)}·{fmt(s)})",
                                    Fd / (2 * d * s), "МПа")
        result.check("Смятие в щеке вилки", cheek_bearing, lug.yield_, "МПа", n_req, "σт")
        cheek_net = Fd / 2 / ((b - d) * s)
        result.check("Разрыв щеки у отверстия", cheek_net, lug.yield_, "МПа", n_req, "σт")
        result.check("Срез перемычки щеки", Fd / 2 / (2 * (e - d / 2) * s), 0.6 * lug.yield_, "МПа", n_req, "0,6·σт")

    if v["cheek"] <= 0:
        result.notes.append(f"Толщина щеки не задана — взята {fmt(s)} мм" + (" (t/2)" if double else " (t)"))
    if v["width"] <= 0 or v["edge"] <= 0:
        result.notes.append(f"Ширина проушины {fmt(b)} мм, до края {fmt(e)} мм" + (" — по умолчанию (2,5·d и b/2)"
                            if v["width"] <= 0 else ""))
    if e < d:
        result.notes.append("Перемычка за отверстием меньше диаметра пальца (e < d): по правилам для проушин "
                            "её делают не меньше d — проушину может «разогнуть»")
    result.notes.append("Отверстие взято равным пальцу; зазор увеличивает смятие у края — при посадке с зазором "
                        "и в подвижном шарнире (качание под нагрузкой) берите запас больше или втулку")
    result.notes.append(f"Палец: {pin.describe()}. Проушины: {lug.describe()}")

    if suggest and result.margin < n_req:
        for size in PIN_DIAMETERS:
            if size <= d:
                continue
            r = _calculate({**v, "diameter": float(size)}, [], suggest=False)
            pin_checks = [c for c in r.checks if "пальца" in c.title]
            if min(c.margin for c in pin_checks) >= n_req:
                result.suggestions.append(f"палец ⌀{size} — запас пальца {fmt(min(c.margin for c in pin_checks), 2)}")
                break
        for extra in range(1, 41):
            t2 = t + 2 * extra
            r = _calculate({**v, "lug": t2, "cheek": v["cheek"] or (t2 / 2 if double else 0)}, [], suggest=False)
            lug_checks = [c for c in r.checks if "пальца" not in c.title]
            if lug_checks and min(c.margin for c in lug_checks) >= n_req:
                result.suggestions.append(f"проушина {fmt(t2)} мм — запас проушин {fmt(min(c.margin for c in lug_checks), 2)}")
                break
        stronger = strength_mod.stronger(pin)
        if stronger:
            r = _calculate({**v, "pin_material": stronger[-1].material.short}, [], suggest=False)
            result.suggestions.append(f"палец из {stronger[-1].name} — запас {fmt(r.margin, 2)}")
    return result
