"""Beams on any supports: a spar of a trailer on its axles with the drawbar overhang, a console, a span.

The beam is split into short Euler–Bernoulli elements (the stiffness method):
reactions, bending moments, shear forces and deflections come out exact at
the nodes for point loads, moments and uniform loads on any part of the
length, with hinged supports and fixed ends anywhere.  The checks
(machine building, against the yield point):
    σ = kд·Mmax/Wx ≤ σт/[n],   τ = kд·Qmax·Sx/(Ix·s) ≤ 0,58·σт/[n]
    f ≤ l/[f] for each span, 2·l/[f] for an overhang (СП 20.13330, табл. Д.1)
The deflection is from the loads as given (without kд).
With the distance between the restraints of the compressed flange an open
section is checked for lateral-torsional buckling, with the bearing length of
the forces and supports — for the local stress in the web (calc/stability.py).

Supports: «500; 4500» — hinges at 500 and 4500 mm; «заделка 0» — a fixed end.
Loads: «20 кН в 1500» — a force at 1500 mm; «5 кН/м» — along the whole beam;
«3 кН/м от 0 до 2000»; «2 кН·м в 1000» — a moment.  Items are separated by «;».
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from . import linear, sections, stability, units
from . import strength as strength_mod
from .core import CalcError, Result, fmt
from .fields import BOOL, MATERIAL, PROFILE, TEXT, Field, common_fields, read

KIND = "beam"
TITLE = "Балка"
HINGE, FIXED = "шарнир", "заделка"
ELEMENTS = 240

FIELDS = [
    Field("length", "Длина балки L", units.LENGTH, hint="полная длина, мм (можно «6 м»)"),
    Field("supports", "Опоры", TEXT, "", hint="положения опор от левого конца через «;»: «500; 4500»; "
          "«заделка 0» — жёсткая заделка; пусто — шарниры по концам"),
    Field("loads", "Нагрузки", TEXT, hint="через «;»: «20 кН в 1500», «5 кН/м», «3 кН/м от 0 до 2000», «2 кН·м в 1000»; "
          "кгс и тс тоже"),
    Field("profile", "Профиль", PROFILE, hint="швеллер 16, двутавр 20, труба 80х40х3, труба 57х3,5, "
          "гнутый швеллер 100х50х4, полоса 100х10; «плашмя» — изгиб в другой плоскости"),
    Field("material", "Материал", MATERIAL, "Ст3"),
    Field("beams", "Балок делят нагрузку", units.NUMBER, 1.0, hint="два лонжерона рамы — 2"),
    Field("self_weight", "Учесть собственный вес", BOOL, True),
    Field("deflection", "Допустимый прогиб: пролёт /", units.NUMBER, 250.0,
          hint="f ≤ l/250 по умолчанию; СП 20.13330 даёт 1/150–1/250"),
    Field("lateral", "Раскрепления сжатого пояса через", units.LENGTH, 0.0,
          hint="шаг поперечин, мм — для проверки общей устойчивости швеллера и двутавра; 0 — не проверять"),
    Field("bearing", "Длина опирания сил и опор b", units.LENGTH, 0.0,
          hint="мм, на которой сила или опора давит на полку — для местного напряжения в стенке; 0 — не проверять"),
    *common_fields(1.5),
]


@dataclass
class Load:
    kind: str        # "force", "line", "moment"
    value: float     # N, N/mm, N·mm (downward force positive)
    start: float     # mm
    end: float       # mm (= start for a force or a moment)

    def describe(self) -> str:
        if self.kind == "force":
            return f"сила {fmt(self.value / 1e3)} кН в {fmt(self.start)} мм"
        if self.kind == "moment":
            return f"момент {fmt(self.value / 1e6)} кН·м в {fmt(self.start)} мм"
        return f"{fmt(self.value)} кН/м от {fmt(self.start)} до {fmt(self.end)} мм"


_SPLIT = re.compile(r";|,\s+|\s+и\s+|\n")
_NUM = units.NUMBER_RE


def _items(text: str) -> list[str]:
    return [item.strip() for item in _SPLIT.split(text or "") if item.strip()]


def _position(text: str) -> float:
    return units.parse(text.strip(), units.LENGTH)


def parse_supports(text: str, length: float) -> list[tuple[float, str]]:
    if not (text or "").strip():
        return [(0.0, HINGE), (length, HINGE)]
    out = []
    for item in _items(text):
        low = item.lower()
        kind = FIXED if re.search(r"заделк|защемл|жестк|жёстк", low) else HINGE
        match = re.search(rf"({_NUM})\s*(мм|см|м)?\b", low)
        if not match:
            if "лев" in low or "начал" in low:
                out.append((0.0, kind))
                continue
            if "прав" in low or "конц" in low:
                out.append((length, kind))
                continue
            raise CalcError(f"опора «{item}»: нет положения, мм от левого конца")
        position = _position(match.group(0))
        if not 0 <= position <= length + 1e-6:
            raise CalcError(f"опора в {fmt(position)} мм — за пределами балки длиной {fmt(length)} мм")
        out.append((min(position, length), kind))
    return sorted(set(out))


def parse_loads(text: str, length: float) -> list[Load]:
    loads = []
    for item in _items(text):
        low = item.lower().replace("ё", "е")
        match = re.match(rf"\s*(?:сила|нагрузка|распределенная|момент|q|f|p|m)?\s*=?\s*({_NUM}\s*[^\d\s;]+(?:\s*[·*/]\s*[^\d\s;]+)?)", low)
        if not match:
            raise CalcError(f"нагрузка «{item}»: нужна величина с единицей — «20 кН», «5 кН/м», «2 кН·м»")
        quantity = match.group(1).strip()
        rest = low[match.end():]
        kind = units.kind_of(quantity)
        span = re.search(rf"от\s*({_NUM}\s*(?:мм|см|м)?)\s*до\s*({_NUM}\s*(?:мм|см|м)?)", rest) or \
            re.search(rf"({_NUM}\s*(?:мм|см|м)?)\s*[-–—]\s*({_NUM}\s*(?:мм|см|м)?)", rest)
        at = re.search(rf"(?:в|на|@|при|x\s*=|х\s*=)\s*({_NUM}\s*(?:мм|см|м)?)", rest)
        if kind == units.LINE_LOAD:
            q = units.parse(quantity, kind)
            start, end = (_position(span.group(1)), _position(span.group(2))) if span else (0.0, length)
            if not 0 <= start < end <= length + 1e-6:
                raise CalcError(f"нагрузка «{item}»: участок за пределами балки")
            loads.append(Load("line", q, start, min(end, length)))
        elif kind in (units.FORCE, units.MOMENT):
            if not at:
                raise CalcError(f"нагрузка «{item}»: где приложена? «{quantity} в 1500»")
            position = _position(at.group(1))
            if not 0 <= position <= length + 1e-6:
                raise CalcError(f"нагрузка «{item}»: {fmt(position)} мм — за пределами балки")
            loads.append(Load("force" if kind == units.FORCE else "moment", units.parse(quantity, kind),
                              min(position, length), min(position, length)))
        else:
            raise CalcError(f"нагрузка «{item}»: «{quantity}» — не сила (кН, кгс, тс), не кН/м и не кН·м")
    if not loads:
        raise CalcError("нет нагрузок")
    return loads


@dataclass
class Solution:
    x: np.ndarray        # node positions, mm
    deflection: np.ndarray   # mm, downward positive
    moment: np.ndarray   # N·mm at the nodes, sagging positive (the larger in magnitude of the two sides)
    shear: np.ndarray    # N, the larger in magnitude of the two sides at a node
    reactions: list[tuple[float, str, float, float]]   # position, kind, force N (up), moment N·mm


def solve(length: float, supports: list[tuple[float, str]], loads: list[Load], ei: float) -> Solution:
    """The stiffness method on a fine mesh with nodes at every support, force and load edge."""
    points = {0.0, length} | {p for p, _ in supports}
    for load in loads:
        points |= {load.start, load.end}
    points = sorted(points)
    nodes = [points[0]]
    target = length / ELEMENTS
    for a, b in zip(points, points[1:]):
        if b - a < 1e-9:
            continue
        pieces = max(1, math.ceil((b - a) / target))
        nodes += [a + (b - a) * i / pieces for i in range(1, pieces + 1)]
    x = np.array(nodes)
    n = len(x)
    K = np.zeros((2 * n, 2 * n))
    F = np.zeros(2 * n)
    element_loads = []
    for e in range(n - 1):
        le = x[e + 1] - x[e]
        mid = (x[e] + x[e + 1]) / 2
        q = sum(l.value for l in loads if l.kind == "line" and l.start - 1e-9 <= mid <= l.end + 1e-9)
        k = ei / le ** 3 * np.array([[12, 6 * le, -12, 6 * le], [6 * le, 4 * le ** 2, -6 * le, 2 * le ** 2],
                                     [-12, -6 * le, 12, -6 * le], [6 * le, 2 * le ** 2, -6 * le, 4 * le ** 2]])
        f = -q * np.array([le / 2, le ** 2 / 12, le / 2, -le ** 2 / 12])   # q downward, v upward
        idx = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(idx, idx)] += k
        F[idx] += f
        element_loads.append((k, f, idx))
    for load in loads:
        if load.kind in ("force", "moment"):
            node = int(np.argmin(abs(x - load.start)))
            if load.kind == "force":
                F[2 * node] -= load.value
            else:
                F[2 * node + 1] += load.value
    fixed_dofs = []
    for position, kind in supports:
        node = int(np.argmin(abs(x - position)))
        fixed_dofs.append(2 * node)
        if kind == FIXED:
            fixed_dofs.append(2 * node + 1)
    free = [i for i in range(2 * n) if i not in set(fixed_dofs)]
    u = np.zeros(2 * n)
    try:
        u[free] = linear.solve(K[np.ix_(free, free)], F[free])
        if np.abs(u[0::2]).max() > 1e7:   # a mechanism: metres of sag
            raise linear.Singular
    except linear.Singular:
        raise CalcError("балка не закреплена: нужны хотя бы две опоры или заделка") from None
    reaction = K @ u - F
    moment = np.zeros(n)
    shear = np.zeros(n)
    for e, (k, f, idx) in enumerate(element_loads):
        end = k @ u[idx] - f
        for node, m, v in ((e, -end[1], end[0]), (e + 1, end[3], -end[2])):
            if abs(m) > abs(moment[node]):
                moment[node] = m
            if abs(v) > abs(shear[node]):
                shear[node] = v
    reactions = []
    for position, kind in supports:
        node = int(np.argmin(abs(x - position)))
        reactions.append((position, kind, float(reaction[2 * node]),
                          float(reaction[2 * node + 1]) if kind == FIXED else 0.0))
    return Solution(x, -u[0::2], moment, shear, reactions)


def segments(length: float, supports: list[tuple[float, str]]) -> list[tuple[float, float, bool]]:
    """Spans between the supports and the overhangs: (start, end, is_overhang)."""
    positions = sorted({p for p, _ in supports})
    out = [(a, b, False) for a, b in zip(positions, positions[1:]) if b - a > 1e-6]
    if positions[0] > 1e-6:
        out.insert(0, (0.0, positions[0], True))
    if positions[-1] < length - 1e-6:
        out.append((positions[-1], length, True))
    return out


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    inputs = values.inputs("profile", "material", "kd", "beams")
    if not v["supports"].strip():
        inputs.insert(1, ("Опоры", "шарниры по концам"))
    return _calculate(v, inputs, sections.parse(v["profile"]), None, suggest=True)


def _calculate(v: dict, inputs: list, section: sections.Section, material, suggest: bool) -> Result:
    length = v["length"]
    if length <= 0:
        raise CalcError("длина балки должна быть больше нуля")
    supports = parse_supports(v["supports"], length)
    user_loads = parse_loads(v["loads"], length)
    s = strength_mod.strength(material or v["material"], None, ("solid", "sheet"))
    count = max(1.0, v["beams"])
    kd = v["kd"]
    n_req = v["n_required"]
    result = Result(KIND, f"{TITLE}: {section.name}",
                    "машиностроение — запас по пределу текучести; прогиб по СП 20.13330")
    result.inputs = inputs
    loads = [Load(l.kind, l.value / count, l.start, l.end) for l in user_loads]
    if v["self_weight"]:
        mass = section.mass_per_m(s.density)
        q_self = result.step("Собственный вес", "q = m·g", f"{fmt(mass)}·9,81", mass * units.G / 1000, "кН/м")
        loads.append(Load("line", q_self, 0.0, length))
    if count != 1:
        result.notes.append(f"Нагрузки поделены на {fmt(count)} балки")
    ei = s.elastic * section.ix
    sol = solve(length, supports, loads, ei)
    i_m = int(np.argmax(abs(sol.moment)))
    i_q = int(np.argmax(abs(sol.shear)))
    m_max = float(abs(sol.moment[i_m]))
    q_max = float(abs(sol.shear[i_q]))
    result.step(f"Наибольший изгибающий момент (в {fmt(sol.x[i_m])} мм)", "Mmax", "", m_max / 1e6, "кН·м")
    result.step(f"Наибольшая поперечная сила (в {fmt(sol.x[i_q])} мм)", "Qmax", "", q_max / 1e3, "кН")
    sigma = result.step("Нормальное напряжение", "σ = kд·Mmax/Wx",
                        f"{fmt(kd)}·{fmt(m_max)}/{fmt(section.wx)}", kd * m_max / section.wx, "МПа")
    tau = result.step("Касательное напряжение", "τ = kд·Qmax·Sx/(Ix·s)",
                      f"{fmt(kd)}·{fmt(q_max)}·{fmt(section.sx)}/({fmt(section.ix)}·{fmt(section.web)})",
                      kd * q_max * section.sx / (section.ix * section.web), "МПа")
    result.check("Прочность при изгибе", sigma, s.yield_, "МПа", n_req, "σт")
    result.check("Прочность на срез", tau, 0.58 * s.yield_, "МПа", n_req, "0,58·σт")
    worst = None
    for a, b, overhang in segments(length, supports):
        mask = (sol.x >= a - 1e-6) & (sol.x <= b + 1e-6)
        f = float(np.max(abs(sol.deflection[mask])))
        span = 2 * (b - a) if overhang else b - a
        limit = span / v["deflection"]
        if worst is None or f / limit > worst[0]:
            worst = (f / limit, f, limit, a, b, overhang)
    _, f, limit, a, b, overhang = worst
    where = f"консоль {fmt(a)}–{fmt(b)} мм" if overhang else f"пролёт {fmt(a)}–{fmt(b)} мм"
    result.step(f"Наибольший прогиб ({where})", "f", "", f, "мм")
    result.check(f"Прогиб ({where})", f, limit, "мм", 1.0,
                 f"{'2·' if overhang else ''}l/{fmt(v['deflection'])}")
    _stability(result, v, section, s, sol, user_loads, count, kd, n_req, m_max)
    result.tables.append(("Реакции опор", ["Опора, мм", "Тип", "Реакция, кН", "Момент, кН·м"],
                          [[fmt(p), kind, fmt(r / 1e3), fmt(m / 1e6) if kind == FIXED else ""]
                           for p, kind, r, m in sol.reactions]))
    result.diagram = {"x": sol.x.tolist(), "M": (sol.moment / 1e6).tolist(), "Q": (sol.shear / 1e3).tolist(),
                      "f": sol.deflection.tolist(), "supports": supports}
    result.notes.append(section.describe())
    result.notes.append(s.describe())
    if section.open and not v["lateral"]:
        result.notes.append("Общая устойчивость (боковое выпучивание) открытого профиля не проверялась: задайте шаг "
                            "раскреплений сжатого пояса (поперечин)")
    if suggest:
        _suggest(result, v, section, s, n_req)
    return result


def c1_of(loads: list[Load]) -> tuple[float, str]:
    """C1 of the lateral-torsional buckling by the loads: a single force — 1,35, moments — 1,0, else 1,13."""
    forces = [l for l in loads if l.kind == "force"]
    if any(l.kind == "moment" for l in loads):
        return stability.C1["равномерный момент"], "равномерный момент"
    if len(forces) == 1 and not any(l.kind == "line" for l in loads):
        return stability.C1["сила в середине"], "сила в середине"
    return stability.C1["распределённая нагрузка"], "распределённая нагрузка"


def _stability(result: Result, v: dict, section: sections.Section, s, sol: "Solution", loads: list[Load],
               count: float, kd: float, n_req: float, m_max: float) -> None:
    if v["lateral"] and section.open:
        c1, case = c1_of(loads)
        lef = v["lateral"]
        result.step(f"Критический момент ({case}: C1 = {fmt(c1)}, lef = {fmt(lef)} мм)",
                    "Mcr = C1·π²·E·Iy/lef²·√(Iw/Iy + lef²·G·J/(π²·E·Iy))", "",
                    stability.critical_moment(section, lef, s.elastic, c1) / 1e6, "кН·м")
        phi1, phib = stability.phi_b(section, lef, s.elastic, s.yield_, c1)
        half = "·0,5 (швеллер)" if section.family != sections.I_BEAM else ""
        result.step(f"Коэффициент устойчивости при изгибе (φ1 = Mcr/(Wx·σт){half} = {fmt(phi1)})",
                    "φb (СП 16, прил. Ж)", "", phib, "")
        result.check("Общая устойчивость (изгибно-крутильная)", kd * m_max / 1e6, phib * section.wx * s.yield_ / 1e6,
                     "кН·м", n_req, "φb·Wx·σт")
    if v["bearing"] and section.family not in (sections.STRIP, sections.ROUND):
        forces = [l.value / count for l in loads if l.kind == "force"]
        reactions = [abs(r) for _, _, r, _ in sol.reactions]
        force = max(forces + reactions)
        lef = stability.local_length(section, v["bearing"])
        sigma = result.step("Местное напряжение в стенке", "σloc = kд·F/(tw·lef), lef = b + 2·tf",
                            f"{fmt(kd)}·{fmt(force)}/({fmt(section.web)}·{fmt(lef)})",
                            kd * stability.local_stress(section, force, v["bearing"]), "МПа")
        result.check("Местное напряжение под силой или опорой", sigma, s.yield_, "МПа", n_req, "σт")


def _suggest(result: Result, v: dict, section: sections.Section, s, n_req: float) -> None:
    def fits(r: Result) -> bool:
        return all(c.margin >= c.required for c in r.checks)

    if fits(result):
        if result.margin > 3 * n_req:
            for smaller in sorted((x for x in _family(section) if x.area < section.area), key=lambda x: x.area):
                r = _calculate(v, [], smaller, None, suggest=False)
                if fits(r):
                    result.suggestions.append(f"хватит {smaller.name} (запас {fmt(r.margin, 2)}, "
                                              f"{fmt(smaller.mass_per_m(s.density))} кг/м)")
                    break
        return
    for bigger in sections.bigger(section):
        r = _calculate(v, [], bigger, None, suggest=False)
        if fits(r):
            result.suggestions.append(f"{bigger.name} — запас {fmt(r.margin, 2)}, "
                                      f"{fmt(bigger.mass_per_m(s.density))} кг/м")
            break
    else:
        result.suggestions.append("в сортаменте этого профиля подходящего нет — нужен другой профиль, "
                                  "больше балок или опор")
    deflection_ok = all(c.margin >= c.required for c in result.checks if c.title.startswith("Прогиб"))
    if deflection_ok:
        for better in strength_mod.stronger(s):
            r = _calculate(v, [], section, better.material, suggest=False)
            if fits(r):
                result.suggestions.append(f"{better.name} вместо {s.name} — запас {fmt(r.margin, 2)}")
                break
    else:
        result.notes.append("Прогиб зависит от жёсткости, не от марки: более прочная сталь его не уменьшит")


def _family(section: sections.Section) -> list[sections.Section]:
    """All the sections of the family of *section* (for a lighter one when the margin is large)."""
    smallest = {
        sections.CHANNEL: "швеллер 5", sections.I_BEAM: "двутавр 10", sections.ROUND: "круг 10",
    }.get(section.family)
    if smallest:
        base = sections.parse(smallest)
        if section.name.endswith("(плашмя)"):
            base = base.turned()
        return [base] + sections.bigger(base)
    return []
