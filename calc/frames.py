"""A trailer frame as a whole: spars, cross-members and a V-drawbar on the axles.

Seen from above and loaded from above, the frame is a grillage — a plane frame
loaded across its plane: each node moves up and down (w) and turns about the
two horizontal axes, each member bends in its vertical plane and twists.  The
stiffness method with members split into short elements gives the reactions
of the axles, in every member the bending moment, the shear force and the
torque, and the deflections.  A cross-member welded to the spars is rigid
(moments and torques pass the joint); a bolted one is hinged (only the shear).

Checks of every member (machine building, against σт):
    σ = kд·M/Wx,  τ = kд·(Q·Sx/(Ix·s) + T/Wt),  σэкв = √(σ² + 3·τ²) ≤ σт/[n]
    open sections — lateral-torsional buckling between the cross-members (spars), along the width
    (cross-members), along the drawbar (calc/stability.py); the free torsion only: the warping stresses
    of open sections in torsion are not counted (noted)
    the deflection of the spars between the axles l/[f] and of their overhangs 2·l/[f]
    the welds of the cross-members all round with the leg k: τ = V/Aw + M/Ww + T·r/Ip ≤ 0,6·σт/[n]
    the local stress in the spar web over an axle with the bearing length b
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import beams, linear, sections, stability, units
from . import strength as strength_mod
from .core import CalcError, Result, fmt
from .fields import BOOL, CHOICE, MATERIAL, PROFILE, TEXT, Field, common_fields, read

KIND = "frame"
TITLE = "Рама прицепа"
RIGID, HINGED = "жёсткие (сварка)", "шарнирные (болты)"
SPAR, CROSS, DRAWBAR = "spar", "cross", "drawbar"
SUBDIVIDE = 4
RELEASED_TORSION = 1e-6   # a bolted cross-member: almost no torsion through the joint

FIELDS = [
    Field("length", "Длина лонжеронов L", units.LENGTH, hint="от переднего конца рамы, мм (можно «6 м»)"),
    Field("width", "Расстояние между лонжеронами B", units.LENGTH, hint="по осям лонжеронов, мм"),
    Field("axles", "Оси", TEXT, hint="положения осей от переднего конца через «;»: «2800; 3600»"),
    Field("crossmembers", "Поперечины", TEXT, "",
          hint="положения через «;»: «0; 1000; 2000; …» или шаг: «через 1000»; пусто — по концам и у осей"),
    Field("drawbar", "Длина дышла a", units.LENGTH, 0.0, hint="V-образное дышло от переднего конца рамы до сцепки; 0 — нет"),
    Field("coupling", "Сцепка несёт вертикальную нагрузку", BOOL, False,
          hint="да — одноосный прицеп (центральная ось); нет — у сцепки опоры нет"),
    Field("spar", "Профиль лонжеронов", PROFILE, hint="швеллер 16, труба 120х60х4, гнутый швеллер 160х60х5"),
    Field("cross", "Профиль поперечин", PROFILE, hint="труба 60х40х3, швеллер 8"),
    Field("drawbar_profile", "Профиль дышла", PROFILE, "", hint="пусто — как у лонжеронов"),
    Field("joints", "Узлы поперечин", CHOICE, RIGID, (RIGID, HINGED)),
    Field("body_load", "Груз на платформе (весь)", units.FORCE, 0.0,
          hint="равномерно по лонжеронам, кН или тс; кузов и борта тоже сюда"),
    Field("loads", "Сосредоточенные грузы", TEXT, "",
          hint="через «;»: «1 тс в 2000» (по центру), «500 кгс в 3000 слева», «2 кН в 1000 справа»"),
    Field("material", "Материал", MATERIAL, "Ст3"),
    Field("self_weight", "Учесть собственный вес", BOOL, True),
    Field("deflection", "Допустимый прогиб: пролёт /", units.NUMBER, 250.0),
    Field("weld_leg", "Катет сварки поперечин k", units.LENGTH, 0.0, hint="для проверки сварки поперечин к лонжеронам; 0 — не проверять"),
    Field("bearing", "Длина опирания на ось b", units.LENGTH, 0.0,
          hint="мм — для местного напряжения в стенке лонжерона над осью; 0 — не проверять", advanced=True),
    *common_fields(1.5),
]


@dataclass
class Member:
    role: str
    group: str
    section: sections.Section
    start: tuple[float, float]
    end: tuple[float, float]
    hinged: bool = False


@dataclass
class Element:
    member: Member
    nodes: tuple[int, int]
    length: float
    cos: float
    sin: float
    q: float = 0.0                      # N/mm downward
    hinge_start: bool = False
    hinge_end: bool = False
    forces: np.ndarray = field(default_factory=lambda: np.zeros(6))   # local end forces


@dataclass
class Grillage:
    nodes: list[tuple[float, float]]
    elements: list[Element]
    supports: list[int]
    w: np.ndarray
    reactions: dict[int, float]


def _positions(text: str, what: str) -> list[float]:
    items = [x for x in re.split(r";|,\s+|\n", text or "") if x.strip()]
    try:
        return [units.parse(x, units.LENGTH) for x in items]
    except CalcError:
        raise CalcError(f"{what} «{text}»: нужны положения через «;» — «2800; 3600»") from None


def cross_positions(text: str, length: float) -> list[float]:
    """«0; 1000; 2000» or a step «через 1000» / «шаг 1000» (from the front end, the rear end too)."""
    match = re.match(rf"\s*(?:через|шаг)\s*({units.NUMBER_RE}\s*(?:мм|см|м)?)\s*$", (text or "").lower())
    if not match:
        return _positions(text, "поперечины")
    step = units.parse(match.group(1), units.LENGTH)
    if step <= 0:
        raise CalcError("шаг поперечин должен быть больше нуля")
    count = int(length // step + 1e-9)
    return sorted({*(i * step for i in range(count + 1)), length})


def parse_point_loads(text: str) -> list[tuple[float, float, str]]:
    """«1 тс в 2000 слева» -> (N, x, side) with side "left", "right" or "center"."""
    out = []
    for item in (x for x in re.split(r";|\n", text or "") if x.strip()):
        low = item.lower().replace("ё", "е")
        match = re.match(rf"\s*({units.NUMBER_RE}\s*[^\d\s;]+)\s*(?:в|на|@|при)\s*({units.NUMBER_RE}\s*(?:мм|см|м)?)", low)
        if not match or units.kind_of(match.group(1)) != units.FORCE:
            raise CalcError(f"груз «{item.strip()}»: пишите «1 тс в 2000», «500 кгс в 3000 слева»")
        side = "left" if "лев" in low else "right" if "прав" in low else "center"
        out.append((units.parse(match.group(1), units.FORCE), units.parse(match.group(2), units.LENGTH), side))
    return out


class _Builder:
    def __init__(self) -> None:
        self.nodes: list[tuple[float, float]] = []
        self.index: dict[tuple[float, float], int] = {}
        self.elements: list[Element] = []

    def node(self, x: float, y: float) -> int:
        key = (round(x, 3), round(y, 3))
        if key not in self.index:
            self.index[key] = len(self.nodes)
            self.nodes.append((x, y))
        return self.index[key]

    def member(self, member: Member, stations: list[float], q: float = 0.0) -> None:
        """*stations* — distances along the member where it has nodes (its ends included)."""
        (x1, y1), (x2, y2) = member.start, member.end
        length = math.hypot(x2 - x1, y2 - y1)
        c, s = (x2 - x1) / length, (y2 - y1) / length
        points = sorted({0.0, length} | {p for p in stations if 0 < p < length})
        fine = []
        for a, b in zip(points, points[1:]):
            fine += [a + (b - a) * i / SUBDIVIDE for i in range(SUBDIVIDE)]
        fine.append(length)
        ids = [self.node(x1 + c * d, y1 + s * d) for d in fine]
        for i, (a, b) in enumerate(zip(ids, ids[1:])):
            self.elements.append(Element(member, (a, b), fine[i + 1] - fine[i], c, s, q,
                                         member.hinged and i == 0, member.hinged and i == len(ids) - 2))


def _element_matrix(e: Element, ei: float, gj: float) -> tuple[np.ndarray, ...]:
    """Global stiffness and load vector, the transformation, local stiffness and load vector of an element.

    DOF at each end: w and the slopes ∂w/∂x, ∂w/∂y; locally w, the slope along the member (bending)
    and the slope across it (the twist of the member)."""
    L = e.length
    k = np.zeros((6, 6))
    bend = ei / L ** 3 * np.array([[12, 6 * L, -12, 6 * L], [6 * L, 4 * L ** 2, -6 * L, 2 * L ** 2],
                                   [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L ** 2, -6 * L, 4 * L ** 2]])
    idx = [0, 1, 3, 4]
    k[np.ix_(idx, idx)] = bend
    k[np.ix_([2, 5], [2, 5])] = gj / L * np.array([[1, -1], [-1, 1]])
    f = np.zeros(6)
    f[idx] = -e.q * np.array([L / 2, L ** 2 / 12, L / 2, -L ** 2 / 12])
    for released, r in ((e.hinge_start, 1), (e.hinge_end, 4)):
        if released:   # a hinge: no bending moment at this end — condense the slope out
            col = k[:, r].copy()
            f = f - col * f[r] / k[r, r]
            k = k - np.outer(col, k[r, :]) / k[r, r]
            k[r, :] = k[:, r] = 0.0
            f[r] = 0.0
    c, s = e.cos, e.sin
    node_t = np.array([[1, 0, 0], [0, c, s], [0, -s, c]])   # local (w, slope along, slope across) of global (w, ∂w/∂x, ∂w/∂y)
    t = np.zeros((6, 6))
    t[:3, :3] = node_t
    t[3:, 3:] = node_t
    return t.T @ k @ t, t.T @ f, t, k, f


def solve(nodes: list[tuple[float, float]], elements: list[Element], supports: list[int], point_loads: dict[int, float],
          elastic: float, hold_roll: list[int] = ()) -> Grillage:
    """*hold_roll* — the nodes where the spar cannot roll about its axis (the axle hangers of a frame with bolted
    cross-members: nothing else holds the spar then)."""
    n = len(nodes)
    K = np.zeros((3 * n, 3 * n))
    F = np.zeros(3 * n)
    cache = []
    for e in elements:
        sec = e.member.section
        gj = elastic / 2.6 * sec.j * (RELEASED_TORSION if e.member.hinged else 1.0)
        kg, fg, t, kl, fl = _element_matrix(e, elastic * sec.ix, gj)
        dofs = [3 * e.nodes[0] + i for i in range(3)] + [3 * e.nodes[1] + i for i in range(3)]
        K[np.ix_(dofs, dofs)] += kg
        F[dofs] += fg
        cache.append((e, dofs, t, kl, fl))
    for node, force in point_loads.items():
        F[3 * node] -= force
    fixed = {3 * i for i in supports} | {3 * i + 2 for i in hold_roll}
    # a node only hinged cross-members meet has no stiffness for its slopes: hold them
    for i in range(n):
        for d in (1, 2):
            if K[3 * i + d, 3 * i + d] == 0.0:
                fixed.add(3 * i + d)
    # numbered along the frame: the matrix is banded (calc/linear.py)
    free = sorted((i for i in range(3 * n) if i not in fixed), key=lambda i: (nodes[i // 3][0], nodes[i // 3][1], i))
    u = np.zeros(3 * n)
    try:
        u[free] = linear.solve(K[np.ix_(free, free)], F[free])
        if np.abs(u[0::3]).max() > 1e7:   # a mechanism: metres of sag
            raise linear.Singular
    except linear.Singular:
        raise CalcError("рама не закреплена: на одной оси без опоры у сцепки она опрокидывается — "
                        "включите «Сцепка несёт вертикальную нагрузку» или добавьте ось") from None
    reaction = K @ u - F
    for e, dofs, t, kl, fl in cache:
        e.forces = kl @ (t @ u[dofs]) - fl
    return Grillage(nodes, elements, supports, -u[0::3], {i: float(reaction[3 * i]) for i in supports})


def interior_moment(e: Element) -> float:
    """The extreme bending moment inside an element under its uniform load (0 if it is at an end):
    with the moments at the ends M1, M2, the shear at the start V = (M2 − M1 + q·L²/2)/L and M* = M1 + V²/(2·q)."""
    if e.q <= 0:
        return 0.0
    m1, m2 = -e.forces[1], e.forces[4]
    shear = (m2 - m1 + e.q * e.length ** 2 / 2) / e.length
    x = shear / e.q
    return m1 + shear ** 2 / (2 * e.q) if 0 < x < e.length else 0.0


@dataclass
class Forces:
    moment: float = 0.0     # N·mm, the largest along the member
    shear: float = 0.0      # N
    torque: float = 0.0     # N·mm


def member_forces(grid: Grillage) -> dict[str, Forces]:
    out: dict[str, Forces] = {}
    for e in grid.elements:
        f = out.setdefault(e.member.group, Forces())
        m = max(abs(e.forces[1]), abs(e.forces[4]), abs(interior_moment(e)))
        v = max(abs(e.forces[0]), abs(e.forces[3]))
        t = abs(e.forces[2])
        f.moment, f.shear, f.torque = max(f.moment, m), max(f.shear, v), max(f.torque, t)
    return out


def calculate(raw: dict) -> Result:
    values = read(FIELDS, raw)
    v = values.values
    profiles = {SPAR: sections.parse(v["spar"]), CROSS: sections.parse(v["cross"])}
    profiles[DRAWBAR] = sections.parse(v["drawbar_profile"]) if v["drawbar_profile"] else profiles[SPAR]
    inputs = values.inputs("spar", "cross", "joints", "material", "kd")
    return _calculate(v, inputs, profiles, None, suggest=True)


def _geometry(v: dict, profiles: dict) -> tuple[_Builder, list[int], dict[int, float], list[str], list[float], list[float]]:
    L, B = v["length"], v["width"]
    if L <= 0 or B <= 0:
        raise CalcError("длина и ширина рамы должны быть больше нуля")
    axles = _positions(v["axles"], "оси")
    if not axles:
        raise CalcError("нужна хотя бы одна ось")
    for x in axles:
        if not 0 <= x <= L:
            raise CalcError(f"ось в {fmt(x)} мм — вне рамы длиной {fmt(L)} мм")
    cross = cross_positions(v["crossmembers"], L) or sorted({0.0, L, *axles})
    cross = sorted({min(max(x, 0.0), L) for x in cross})
    loads = parse_point_loads(v["loads"])
    notes = []
    hinged = v["joints"] == HINGED
    builder = _Builder()
    y_left, y_right = -B / 2, B / 2
    stations = set(cross) | set(axles) | {x for _, x, _ in loads}
    q_body = v["body_load"] / (2 * L)
    for y, name in ((y_left, "Лонжерон левый"), (y_right, "Лонжерон правый")):
        builder.member(Member(SPAR, name, profiles[SPAR], (0.0, y), (L, y)), list(stations), q_body)
    for x in cross:
        builder.member(Member(CROSS, f"Поперечина {fmt(x)}", profiles[CROSS], (x, y_left), (x, y_right), hinged),
                       [B / 2])
    supports = [builder.node(x, y) for x in axles for y in (y_left, y_right)]
    if v["drawbar"] > 0:
        apex = (-v["drawbar"], 0.0)
        for y in (y_left, y_right):
            builder.member(Member(DRAWBAR, "Дышло", profiles[DRAWBAR], (0.0, y), apex), [])
        if v["coupling"]:
            supports.append(builder.node(*apex))
    elif v["coupling"]:
        raise CalcError("сцепка без дышла: задайте длину дышла")
    point: dict[int, float] = {}
    for force, x, side in loads:
        if not 0 <= x <= L:
            raise CalcError(f"груз в {fmt(x)} мм — вне рамы")
        if side == "center" and any(abs(c - x) < 1 for c in cross):
            node = builder.node(x, 0.0)
            point[node] = point.get(node, 0.0) + force
            continue
        if side == "center":
            notes.append(f"Груз в {fmt(x)} мм по центру: поперечины там нет — поделён поровну на лонжероны")
            for y in (y_left, y_right):
                node = builder.node(x, y)
                point[node] = point.get(node, 0.0) + force / 2
            continue
        node = builder.node(x, y_left if side == "left" else y_right)
        point[node] = point.get(node, 0.0) + force
    return builder, supports, point, notes, axles, cross


def _calculate(v: dict, inputs: list, profiles: dict, material, suggest: bool) -> Result:
    s = strength_mod.strength(material or v["material"], None, ("solid", "sheet"))
    kd, n_req = v["kd"], v["n_required"]
    builder, supports, point, notes, axles, cross = _geometry(v, profiles)
    if v["self_weight"]:
        for e in builder.elements:
            e.q += e.member.section.mass_per_m(s.density) * units.G / 1000
    result = Result(KIND, f"{TITLE}: {fmt(v['length'])}×{fmt(v['width'])} мм, оси {v['axles']}",
                    "машиностроение — запас по пределу текучести; рама как ростверк (метод перемещений): изгиб и "
                    "кручение каждого элемента")
    result.inputs = inputs
    hold = [i for i in supports if builder.nodes[i][0] >= 0] if v["joints"] == HINGED else []
    grid = solve(builder.nodes, builder.elements, supports, point, s.elastic, hold)
    total_load = sum(point.values()) + sum(e.q * e.length for e in builder.elements)
    result.step("Вся нагрузка на раму (с собственным весом)" if v["self_weight"] else "Вся нагрузка на раму",
                "ΣP", "", total_load / 1e3, "кН")
    forces = member_forces(grid)
    rows, joints = [], []
    order = sorted(forces, key=lambda g: ({"Л": 0, "П": 1, "Д": 2}.get(g[0], 3), g))
    for group in order:
        f = forces[group]
        member = next(e.member for e in grid.elements if e.member.group == group)
        sec = member.section
        sigma = kd * f.moment / sec.wx
        tau = kd * (f.shear * sec.sx / (sec.ix * sec.web) + (f.torque / sec.wt if sec.wt else 0.0))
        s_eq = math.sqrt(sigma ** 2 + 3 * tau ** 2)
        check = result.check(f"{group} ({sec.name}): прочность", s_eq, s.yield_, "МПа", n_req,
                             "σт; σэкв = √(σ² + 3τ²)")
        rows.append([group, sec.name, fmt(f.moment / 1e6), fmt(f.shear / 1e3), fmt(f.torque / 1e6), fmt(sigma),
                     fmt(tau), fmt(check.margin, 2)])
        if sec.open:
            if member.role == SPAR:
                spacing = max(b - a for a, b in zip(cross, cross[1:])) if len(cross) > 1 else v["length"]
            elif member.role == CROSS:
                spacing = v["width"]
            else:
                spacing = math.hypot(v["drawbar"], v["width"] / 2)
            _, phib = stability.phi_b(sec, spacing, s.elastic, s.yield_)
            result.check(f"{group}: общая устойчивость (lef {fmt(spacing)} мм)", kd * f.moment / 1e6,
                         phib * sec.wx * s.yield_ / 1e6, "кН·м", n_req, f"φb·Wx·σт, φb = {fmt(phib)}")
        if member.role == CROSS:
            joints.append((group, member, sec))
    result.tables.append(("Усилия в элементах", ["Элемент", "Профиль", "Mmax, кН·м", "Qmax, кН", "Mкр, кН·м",
                                                  "σ, МПа", "τ, МПа", "Запас"], rows))
    reaction_rows = []
    for node, r in grid.reactions.items():
        x, y = grid.nodes[node]
        where = "сцепка" if x < 0 else f"ось {fmt(x)}, {'слева' if y < 0 else 'справа'}"
        reaction_rows.append([where, fmt(r / 1e3)])
    result.tables.append(("Реакции опор", ["Опора", "Реакция, кН"], reaction_rows))
    _deflection(result, v, grid, axles)
    if v["weld_leg"]:
        _joint_welds(result, v, grid, joints, s, kd, n_req)
    if v["bearing"]:
        sec = profiles[SPAR]
        r_max = max(abs(r) for node, r in grid.reactions.items() if grid.nodes[node][0] >= 0)
        sigma = result.step("Местное напряжение в стенке лонжерона над осью", "σloc = kд·R/(tw·(b + 2·tf))",
                            f"{fmt(kd)}·{fmt(r_max)}/({fmt(sec.web)}·{fmt(stability.local_length(sec, v['bearing']))})",
                            kd * stability.local_stress(sec, r_max, v["bearing"]), "МПа")
        result.check("Местное напряжение над осью", sigma, s.yield_, "МПа", n_req, "σт")
    apex = [i for i, (x, _) in enumerate(grid.nodes) if x < 0]
    if apex and not v["coupling"]:
        notes.append(f"Конец дышла опускается на {fmt(abs(grid.w[apex[-1]]))} мм — у сцепки опоры нет")
    if any(m.open for m in profiles.values()):
        notes.append("Кручение открытых профилей (швеллеров) считается как свободное: напряжения стеснённого "
                     "кручения не учтены — для поперечин из швеллеров запас на деле меньше")
    hinged = v["joints"] == HINGED
    if hinged:
        notes.append("Узлы шарнирные: поперечины передают на лонжероны только вертикальную силу; от опрокидывания "
                     "лонжероны держат подвески осей")
    result.notes += notes
    result.notes += [m.describe() for m in {id(m): m for m in profiles.values()}.values()]
    result.notes.append(s.describe())
    result.diagram = {"type": "frame",
                      "members": [(*grid.nodes[e.nodes[0]], *grid.nodes[e.nodes[1]],
                                   _ratio(result, e.member.group)) for e in grid.elements],
                      "supports": [grid.nodes[i] for i in grid.supports],
                      "loads": [grid.nodes[i] for i in point]}
    if suggest:
        _suggest(result, v, profiles, n_req)
    return result


def _ratio(result: Result, group: str) -> float:
    """How loaded a member is: required / margin of its worst check (1 — at the limit)."""
    checks = [c for c in result.checks if c.title.startswith(group)]
    return max((c.required / c.margin if c.margin else 0.0) for c in checks) if checks else 0.0


def _deflection(result: Result, v: dict, grid: Grillage, axles: list[float]) -> None:
    supports = [(x, beams.HINGE) for x in sorted(set(axles))]
    if len(supports) == 1 and not v["coupling"]:
        supports = supports + supports
    worst = None
    for y in (-v["width"] / 2, v["width"] / 2):
        line = sorted((x, abs(w)) for (x, yy), w in zip(grid.nodes, grid.w) if abs(yy - y) < 1e-6 and x >= 0)
        for a, b, overhang in beams.segments(v["length"], supports):
            inside = [w for x, w in line if a - 1e-6 <= x <= b + 1e-6]
            if not inside:
                continue
            f = max(inside)
            limit = (2 * (b - a) if overhang else b - a) / v["deflection"]
            if worst is None or f / limit > worst[0]:
                worst = (f / limit, f, limit, a, b, overhang)
    if worst is None:
        return
    _, f, limit, a, b, overhang = worst
    where = f"{'консоль' if overhang else 'пролёт'} лонжерона {fmt(a)}–{fmt(b)} мм"
    result.step(f"Наибольший прогиб ({where})", "f", "", f, "мм")
    result.check(f"Прогиб ({where})", f, limit, "мм", 1.0, f"{'2·' if overhang else ''}l/{fmt(v['deflection'])}")


def joint_weld_stress(sec: sections.Section, leg: float, shear: float, moment: float, torque: float) -> float:
    """τ in a fillet weld all round the end of a member h×b: V/Aw + M/Ww + T·r/Ip (the throat 0,7·k)."""
    h, b, a = sec.h, sec.b, 0.7 * leg
    area = a * 2 * (h + b)
    ix = a * (2 * b * (h / 2) ** 2 + h ** 3 / 6)
    iy = a * (2 * h * (b / 2) ** 2 + b ** 3 / 6)
    r = math.hypot(h / 2, b / 2)
    return shear / area + moment / (ix / (h / 2)) + torque * r / (ix + iy)


def _joint_welds(result: Result, v: dict, grid: Grillage, joints: list, s, kd: float, n_req: float) -> None:
    leg = v["weld_leg"]
    for group, member, sec in joints:
        elements = [e for e in grid.elements if e.member is member]
        first, last = elements[0], elements[-1]
        worst = 0.0
        for shear, moment, torque in ((first.forces[0], first.forces[1], first.forces[2]),
                                      (last.forces[3], last.forces[4], last.forces[5])):
            if member.hinged:
                moment = torque = 0.0
            worst = max(worst, joint_weld_stress(sec, leg, abs(shear), abs(moment), abs(torque)))
        result.check(f"{group}: сварка к лонжерону (k = {fmt(leg)})", kd * worst, 0.6 * s.yield_, "МПа", n_req,
                     "0,6·σт; τ = V/Aw + M/Ww + T·r/Ip")


def _suggest(result: Result, v: dict, profiles: dict, n_req: float) -> None:
    failing = [c for c in result.checks if c.margin < c.required]
    if not failing:
        return
    roles = []
    for c in failing:
        role = SPAR if c.title.startswith(("Лонжерон", "Прогиб", "Местное")) else \
            DRAWBAR if c.title.startswith("Дышло") else CROSS
        if role not in roles:
            roles.append(role)
    names = {SPAR: "лонжероны", CROSS: "поперечины", DRAWBAR: "дышло"}
    for role in roles:
        found = False
        for bigger in sections.bigger(profiles[role])[:40]:
            trial = {**profiles, role: bigger}
            if role == SPAR and not v["drawbar_profile"]:
                trial[DRAWBAR] = bigger
            try:
                r = _calculate(v, [], trial, None, suggest=False)
            except CalcError:
                continue
            prefix = {SPAR: ("Лонжерон", "Прогиб", "Местное"), CROSS: ("Поперечина",), DRAWBAR: ("Дышло",)}[role]
            if all(c.margin >= c.required for c in r.checks if c.title.startswith(prefix)):
                result.suggestions.append(f"{names[role]} — {bigger.name}: запас {fmt(r.margin, 2)}")
                found = True
                break
        if not found:
            result.suggestions.append(f"{names[role]}: в сортаменте этого профиля подходящего нет")
