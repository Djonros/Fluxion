"""«Развесовка прицепа»: the loads on the axles and the coupling from the masses and their
centres of gravity, and the static stability against rolling over.

Positions are measured along the trailer from the coupling (the hitch eye or the
king pin). Axles in a group share the load equally (a balancer, or air suspension
with linked bags), so a group is one support at its middle.

- The coupling carries load (a semitrailer, a centre-axle trailer): two supports —
  the coupling at 0 and the axle group:  R_axles = W·x_c/x_g,  R_coupling = W − R_axles.
- It does not (a drawbar trailer with a turntable front axle): the first axle and the
  group of the others:  R_rear = W·(x_c − x₁)/(x_r − x₁),  R_front = W − R_rear.

Rolling over, static (a rigid body on a rigid road): the lateral acceleration at which
the inner wheels lift, a/g = B/(2·h) — the track over twice the height of the centre of
gravity; the tilt angle atan(B/(2·h)). The suspension and the tyres lower it by about
20–30 %, so the required margin over the threshold asked is 1,25.
"""
from __future__ import annotations

import math

from . import units
from .core import CalcError, Result, fmt
from .fields import BOOL, Field, read

KIND = "axles"
TITLE = "Развесовка прицепа"
ROLL_MARGIN = 1.25

FIELDS = [
    Field("frame", "Масса рамы и надстройки", units.FORCE, hint="тс или кг: 3,5 т"),
    Field("frame_x", "Центр тяжести рамы от сцепки", units.LENGTH),
    Field("cargo", "Масса груза", units.FORCE, 0.0),
    Field("cargo_x", "Центр тяжести груза от сцепки", units.LENGTH, 0.0),
    Field("axles", "Оси от сцепки", "text", hint="через «;»: 4500; 5810; 7120 — все оси по порядку"),
    Field("coupling", "Сцепка несёт вертикальную нагрузку", BOOL, True,
          hint="да — полуприцеп или центральноосный прицеп; нет — прицеп с поворотной передней осью"),
    Field("axle_limit", "Допустимая нагрузка на ось", units.FORCE, "10 тс"),
    Field("coupling_limit", "Допустимая нагрузка на сцепку", units.FORCE, 0.0, hint="0 — не проверять"),
    Field("track", "Колея", units.LENGTH, 0.0, hint="между серединами колёс (сдвоенных — между серединами пар)"),
    Field("height", "Высота центра тяжести от дороги", units.LENGTH, 0.0, hint="с грузом; 0 — устойчивость не проверять"),
    Field("lateral", "Требуемое боковое ускорение, g", units.NUMBER, 0.4,
          hint="порог опрокидывания, который нужен: 0,4 g — обычное требование для грузовых прицепов"),
]


def _positions(text: str) -> list[float]:
    out = []
    for part in str(text or "").replace(",", ".").replace(" ", "").split(";"):
        if part:
            try:
                out.append(units.parse(part.replace(".", ","), units.LENGTH))
            except CalcError:
                raise CalcError(f"оси: «{part}» — нужны расстояния от сцепки через «;»") from None
    return out


def _tf(newton: float) -> float:
    return newton / units.G / 1e3


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    inputs = values.inputs("coupling", "axle_limit")
    axles = sorted(_positions(v["axles"]))
    if not axles:
        raise CalcError("нужны положения осей от сцепки")
    if any(x <= 0 for x in axles):
        raise CalcError("оси отсчитываются от сцепки — расстояния больше нуля")
    if not v["coupling"] and len(axles) < 2:
        raise CalcError("прицеп без нагрузки на сцепку стоит на двух группах осей — нужно не меньше двух осей")
    Wf, Wc = v["frame"], v["cargo"]
    if Wf <= 0:
        raise CalcError("нужна масса рамы больше нуля")
    W = Wf + Wc
    result = Result(KIND, f"{TITLE}: {fmt(_tf(W))} тс, осей {len(axles)}",
                    "статика: оси группы делят нагрузку поровну (балансир); устойчивость — статический порог B/(2h)")
    result.inputs = inputs
    xc = result.step("Центр тяжести всего прицепа от сцепки", "x = (Gр·xр + Gг·xг)/(Gр + Gг)",
                     f"({fmt(_tf(Wf))}·{fmt(v['frame_x'])} + {fmt(_tf(Wc))}·{fmt(v['cargo_x'])})/{fmt(_tf(W))}",
                     (Wf * v["frame_x"] + Wc * v["cargo_x"]) / W, "мм")
    limit = v["axle_limit"]
    rows = []
    if v["coupling"]:
        xg = sum(axles) / len(axles)
        group = result.step("Нагрузка на оси", "Rо = G·x/xгр", f"{fmt(_tf(W))}·{fmt(xc)}/{fmt(xg)}", _tf(W * xc / xg),
                            "тс") * units.G * 1e3
        hitch = result.step("Нагрузка на сцепку", "Rс = G − Rо", f"{fmt(_tf(W))} − {fmt(_tf(group))}", _tf(W - group),
                            "тс") * units.G * 1e3
        per_axle = [group / len(axles)] * len(axles)
        result.step("На каждую ось", "Rо/n", f"{fmt(_tf(group))}/{len(axles)}", _tf(per_axle[0]), "тс")
    else:
        x1, rear = axles[0], axles[1:]
        xr = sum(rear) / len(rear)
        rear_load = result.step("Нагрузка на задние оси", "Rз = G·(x − x₁)/(xз − x₁)",
                                f"{fmt(_tf(W))}·({fmt(xc)} − {fmt(x1)})/({fmt(xr)} − {fmt(x1)})",
                                _tf(W * (xc - x1) / (xr - x1)), "тс") * units.G * 1e3
        front = result.step("Нагрузка на переднюю ось", "Rп = G − Rз", f"{fmt(_tf(W))} − {fmt(_tf(rear_load))}",
                            _tf(W - rear_load), "тс") * units.G * 1e3
        hitch = None
        per_axle = [front] + [rear_load / len(rear)] * len(rear)
    for i, (x, load) in enumerate(zip(axles, per_axle), 1):
        rows.append([str(i), fmt(x), fmt(_tf(load))])
        if load < 0:
            result.check(f"Ось {i} не отрывается", 1.0, 0.0, "", 1.0, "нагрузка ≥ 0")
        else:
            result.check(f"Нагрузка на ось {i}", _tf(load), _tf(limit), "тс", 1.0, "допустимая")
    result.tables.append(("Нагрузки на оси", ["Ось", "От сцепки, мм", "Нагрузка, тс"], rows))
    if hitch is not None:
        if hitch < 0:
            result.check("Сцепка не отрывается вверх", 1.0, 0.0, "", 1.0, "нагрузка ≥ 0")
            result.notes.append("Центр тяжести за осями: сцепку тянет вверх — прицеп «клюёт» назад")
        elif v["coupling_limit"] > 0:
            result.check("Нагрузка на сцепку", _tf(hitch), _tf(v["coupling_limit"]), "тс", 1.0, "допустимая")
        if 0 <= hitch < 0.04 * W:
            result.notes.append("На сцепку меньше 4 % массы: центральноосный прицеп на такой развесовке рыскает — "
                                "сдвиньте груз вперёд")

    if v["track"] > 0 and v["height"] > 0:
        a = result.step("Порог опрокидывания (статический)", "a/g = B/(2·h)", f"{fmt(v['track'])}/(2·{fmt(v['height'])})",
                        v["track"] / (2 * v["height"]), "g")
        result.step("Угол опрокидывания на уклоне", "θ = atan(B/(2·h))", "", math.degrees(math.atan(a)), "°")
        result.check("Устойчивость против опрокидывания", v["lateral"], a, "g", ROLL_MARGIN,
                     "статический порог; подвеска и шины снижают его на 20–30 %")
    else:
        result.notes.append("Колея или высота центра тяжести не заданы — устойчивость не проверена")

    _suggest_cargo(result, v, axles, Wf, Wc)
    return result


def _suggest_cargo(result: Result, v: dict, axles: list[float], Wf: float, Wc: float) -> None:
    """Where the centre of gravity of the cargo may be for every axle and the coupling to pass."""
    if Wc <= 0 or result.status not in ("fail", "low"):
        return
    limit, n = v["axle_limit"], len(axles)
    W = Wf + Wc
    lo, hi = -math.inf, math.inf
    if v["coupling"]:
        xg = sum(axles) / n
        hi = min(hi, (limit * n * xg - Wf * v["frame_x"]) / Wc, (W * xg - Wf * v["frame_x"]) / Wc)
        if v["coupling_limit"] > 0:
            lo = max(lo, ((W - v["coupling_limit"]) * xg - Wf * v["frame_x"]) / Wc)
    else:
        x1, rear = axles[0], axles[1:]
        xr = sum(rear) / len(rear)
        span = xr - x1
        # rear axle each ≤ limit: W·(x − x1)/span ≤ limit·(n−1); front ≤ limit: W − W·(x − x1)/span ≤ limit
        x_hi = x1 + limit * (n - 1) * span / W
        x_lo = x1 + (W - limit) * span / W
        hi = min(hi, (x_hi * W - Wf * v["frame_x"]) / Wc)
        lo = max(lo, (x_lo * W - Wf * v["frame_x"]) / Wc)
    if lo <= hi and hi > 0:
        low = f"от {fmt(max(lo, 0))} " if lo > -math.inf else "до "
        text = f"центр тяжести груза {low}{'до ' if lo > -math.inf else ''}{fmt(hi)} мм от сцепки — нагрузки в норме"
        result.suggestions.append(text.replace("до до", "до"))
    else:
        result.suggestions.append("ни одно положение груза не укладывает все оси в допустимую нагрузку — "
                                  "уменьшите груз или добавьте ось")
