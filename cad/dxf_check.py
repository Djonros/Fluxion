"""«Проверка развёрток»: what a laser or a punch will not make well in a flat pattern.

The DXF of a sheet detail (from «Пакет на лазер», or any folder of them) is read
with ezdxf: lines, arcs, circles, polylines, splines and blocks are flattened into
point chains and joined end to end into closed loops. The biggest loop is the
outer contour, the others are holes and cut-outs. With the thickness t of the sheet
(the folder «…\\8» of the laser package, a number in the file name, or as given):

- a hole smaller than the thickness (d < k·t) — the beam or the punch does not
  make a clean one;
- the web from a hole or a cut-out to the outer edge narrower than k·t;
- the web between two holes narrower than k·t;
- a cut-out narrower than k·t (a slot), by the smaller side of its box.

The factors k are norms of the plant («Нормы и цены завода» → «Лист»), recommended
1·t. With the bend lines (below), as the press brake needs:

- a hole or a cut-out nearer to a bend line than k·t + R (recommended 2·t + R) — it is
  pulled into an oval when bent; R, the inner bend radius, is not in the DXF: R = t;
- a bend line across a hole or a cut-out;
- a flange, from a bend line to the edge or to the next bend, shorter than k·t
  (recommended 3·t) — it does not lie on the die.

Bend lines are not in the DXF of the laser. «Пакет на лазер» saves them apart:
the flat pattern of KOMPAS with its bend lines (the «dash-dot with two dots» line
type) in the subfolder «_гибы» next to the DXF, under the same name. That file is
turned and moved onto the contour of the DXF (a turn by 90° and a mirror are tried)
and its bend lines are kept with the sheet.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

JOIN_TOLERANCE = 0.2  # mm: the ends of two segments of one loop (splines of KOMPAS end a little apart)
FLATTEN = 0.1  # mm: the chord error of arcs and splines

RECOMMENDED_LIMITS = {"hole": 1.0, "edge": 1.0, "between": 1.0, "slot": 1.0, "bend_hole": 2.0, "flange": 3.0}
BENDS_SUBDIR = "_гибы"  # where «Пакет на лазер» puts the bend lines (dxfka/utils.py)
BEND_LINETYPE = "K5LT_CHAIN"  # KOMPAS writes a bend line as K5LT_CHAIN_LINE_WITH_2_POINTS
ALIGN_TOLERANCE = 0.5  # mm: the contour of the bends file on the contour of the DXF


@dataclass
class Loop:
    points: list[tuple[float, float]]
    circle: float = 0.0  # the diameter of a circle; 0 — another shape
    center: tuple[float, float] | None = None  # of a circle: its gaps are measured exactly

    @property
    def area(self) -> float:
        pts = self.points
        return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2

    @property
    def box(self) -> tuple[float, float, float, float]:
        xs, ys = [p[0] for p in self.points], [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def describe(self) -> str:
        if self.circle:
            return f"отверстие ⌀{self.circle:g}"
        x0, y0, x1, y1 = self.box
        return f"вырез {x1 - x0:.1f}×{y1 - y0:.1f}"


@dataclass
class Problem:
    file: str
    thickness: float
    what: str  # «Малое отверстие», «Узкая перемычка до края»…
    size: float  # mm
    limit: float  # mm
    where: str  # «отверстие ⌀6»
    count: int = 1  # the same remark on identical holes of the detail

    @property
    def message(self) -> str:
        times = f" ×{self.count}" if self.count > 1 else ""
        return f"{self.where}{times}: {self.size:.1f} мм при пороге {self.limit:.1f} мм"


@dataclass
class Sheet:
    file: str
    thickness: float
    holes: int = 0
    problems: list[Problem] = field(default_factory=list)
    error: str = ""
    bends: list[tuple[tuple[float, float], tuple[float, float]]] | None = None  # None: no bends file


# ── reading ──────────────────────────────────────────────────────────────────


def _chains(msp) -> tuple[list[list[tuple[float, float]]], list[Loop]]:
    """Open point chains of the drawing and the closed loops it already has (circles, closed polylines)."""
    chains, loops = [], []

    def add(entity) -> None:
        kind = entity.dxftype()
        try:
            if kind == "INSERT":
                for sub in entity.virtual_entities():
                    add(sub)
            elif kind == "CIRCLE":
                r = float(entity.dxf.radius)
                c = entity.dxf.center
                n = max(24, int(2 * math.pi * r / 2))
                loops.append(Loop([(c.x + r * math.cos(2 * math.pi * i / n), c.y + r * math.sin(2 * math.pi * i / n))
                                   for i in range(n)], circle=round(2 * r, 2), center=(c.x, c.y)))
            elif kind == "LINE":
                chains.append([(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)])
            elif kind in ("ARC", "SPLINE", "ELLIPSE"):
                pts = [(p.x, p.y) for p in entity.flattening(FLATTEN)]
                if len(pts) > 1:
                    if kind != "ARC" and math.dist(pts[0], pts[-1]) < JOIN_TOLERANCE:
                        loops.append(Loop(pts[:-1]))
                    else:
                        chains.append(pts)
            elif kind in ("LWPOLYLINE", "POLYLINE"):
                from ezdxf import path as dxf_path  # the bulges (arcs) of a polyline are flattened too

                pts = [(p.x, p.y) for p in dxf_path.make_path(entity).flattening(FLATTEN)]
                closed = bool(getattr(entity, "closed", False) or getattr(entity, "is_closed", False))
                if closed or (len(pts) > 2 and math.dist(pts[0], pts[-1]) < JOIN_TOLERANCE):
                    loops.append(Loop(pts if not math.dist(pts[0], pts[-1]) < JOIN_TOLERANCE else pts[:-1]))
                elif len(pts) > 1:
                    chains.append(pts)
        except Exception:  # an entity ezdxf cannot flatten: the rest is still checked
            pass

    for entity in msp:
        add(entity)
    return chains, loops


def _join(chains: list[list[tuple[float, float]]]) -> list[Loop]:
    """Chains joined end to end into closed loops; what does not close is dropped."""
    loops, pool = [], [list(c) for c in chains]
    while pool:
        loop = pool.pop()
        for _ in range(len(pool) + 1):
            if math.dist(loop[0], loop[-1]) < JOIN_TOLERANCE and len(loop) > 2:
                break
            end = loop[-1]
            for i, chain in enumerate(pool):
                if math.dist(chain[0], end) < JOIN_TOLERANCE:
                    loop += chain[1:]
                elif math.dist(chain[-1], end) < JOIN_TOLERANCE:
                    loop += chain[::-1][1:]
                else:
                    continue
                pool.pop(i)
                break
            else:
                break
        if len(loop) > 2 and math.dist(loop[0], loop[-1]) < JOIN_TOLERANCE:
            loops.append(Loop(loop[:-1]))
    return loops


def read_bends(path: str | Path) -> tuple[list, Loop | None]:
    """(the bend lines, the outer contour) of a bends file of «Пакет на лазер»."""
    import ezdxf

    bends, rest = [], []
    for entity in ezdxf.readfile(str(path)).modelspace():
        if entity.dxftype() == "LINE" and BEND_LINETYPE in str(entity.dxf.get("linetype", "")).upper():
            bends.append(((entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)))
        else:
            rest.append(entity)
    chains, loops = _chains(rest)
    loops = [loop for loop in loops + _join(chains) if loop.area > 0.5]
    return bends, (max(loops, key=lambda loop: loop.area) if loops else None)


# a turn by 0, 90, 180, 270° and the same mirrored: the flat pattern of the bends file
# and the DXF of the laser may lie differently
_TURNS = (lambda x, y: (x, y), lambda x, y: (-y, x), lambda x, y: (-x, -y), lambda x, y: (y, -x),
          lambda x, y: (-x, y), lambda x, y: (y, x), lambda x, y: (x, -y), lambda x, y: (-y, -x))


def align_bends(bends: list, bend_outer: Loop, outer: Loop, tolerance: float = ALIGN_TOLERANCE) -> list | None:
    """The bend lines moved onto *outer*, or None when the two contours are not the same."""
    ox0, oy0, ox1, oy1 = outer.box
    for turn in _TURNS:
        pts = [turn(*p) for p in bend_outer.points]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if abs(max(xs) - min(xs) - (ox1 - ox0)) > tolerance or abs(max(ys) - min(ys) - (oy1 - oy0)) > tolerance:
            continue
        dx, dy = ox0 - min(xs), oy0 - min(ys)
        sample = pts[:: max(1, len(pts) // 60)]
        if sum(_to_loop((x + dx, y + dy), outer) < tolerance for x, y in sample) < 0.9 * len(sample):
            continue

        def move(p, turn=turn, dx=dx, dy=dy):
            x, y = turn(*p)
            return (x + dx, y + dy)

        return [(move(a), move(b)) for a, b in bends]
    return None


def read_loops(path: str | Path) -> list[Loop]:
    import ezdxf

    doc = ezdxf.readfile(str(path))
    chains, loops = _chains(doc.modelspace())
    return loops + _join(chains)


# ── geometry ─────────────────────────────────────────────────────────────────


def _segment_distance(p, a, b) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _to_loop(p, loop: Loop) -> float:
    return min(_segment_distance(p, *s) for s in zip(loop.points, loop.points[1:] + loop.points[:1]))


def _to_bend(loop: Loop, bend) -> float:
    """The least distance from a hole or a cut-out to a bend line; 0 when the line crosses it."""
    a, b = bend
    if loop.circle and loop.center is not None:
        return max(0.0, _segment_distance(loop.center, a, b) - loop.circle / 2)
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    sides, along = set(), []
    for p in loop.points:
        cross = ((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / length
        sides.add(cross > 0)
        along.append(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)
    if len(sides) == 2 and max(along) > 0 and min(along) < length:
        return 0.0  # points on both sides of the line, within its length: the line goes through
    return min(_segment_distance(p, a, b) for p in loop.points)


def _flanges(bend, bends: list, outer: Loop) -> list[float]:
    """The flanges on both sides of a bend line: to the farthest edge of that side, or to the next
    bend line parallel to it, whichever is nearer."""
    a, b = bend
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return []
    ux, uy = dx / length, dy / length
    sides = {True: 0.0, False: 0.0}
    for p in outer.points:
        along = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
        if -0.5 <= along <= length + 0.5:  # the part of the contour beside this bend
            cross = (p[0] - a[0]) * uy - (p[1] - a[1]) * ux
            sides[cross > 0] = max(sides[cross > 0], abs(cross))
    for other in bends:
        if other is bend:
            continue
        (ox, oy), (px, py) = other
        if abs((px - ox) * uy - (py - oy) * ux) > 0.01 * math.hypot(px - ox, py - oy):
            continue  # not parallel
        cross = (ox - a[0]) * uy - (oy - a[1]) * ux
        if abs(cross) > 1e-6:
            sides[cross > 0] = min(sides[cross > 0], abs(cross))
    return [v for v in sides.values() if v > 0]


def gap(a: Loop, b: Loop) -> float:
    """The least distance between two loops (point of one to a segment of the other, both ways);
    a circle by its centre and radius, not by its polygon."""
    if a.center and b.center:
        return max(0.0, math.dist(a.center, b.center) - (a.circle + b.circle) / 2)
    if a.center or b.center:
        circle, other = (a, b) if a.center else (b, a)
        return max(0.0, _to_loop(circle.center, other) - circle.circle / 2)
    def one_way(p_loop: Loop, s_loop: Loop) -> float:
        seg = list(zip(s_loop.points, s_loop.points[1:] + s_loop.points[:1]))
        bx0, by0, bx1, by1 = s_loop.box
        best = math.inf
        for p in p_loop.points:
            # a cheap lower bound by the box first
            box_d = math.hypot(max(bx0 - p[0], 0, p[0] - bx1), max(by0 - p[1], 0, p[1] - by1))
            if box_d >= best:
                continue
            for s in seg:
                d = _segment_distance(p, *s)
                if d < best:
                    best = d
        return best
    return min(one_way(a, b), one_way(b, a))


# ── the check ────────────────────────────────────────────────────────────────


def thickness_of(path: Path, default: float = 0.0) -> float:
    """The folder of the laser package («…\\8\\part.dxf», «…\\1,5\\…»), else «S10», «t8», «s=8», «лист 12»,
    «лист3», «8мм» in the name."""
    try:
        return float(path.parent.name.replace(",", "."))
    except ValueError:
        pass
    match = re.search(r"(?:^|[\s_\-])(?:t|s|толщ\w*|лист)\s*=?\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*мм",
                      path.stem, re.IGNORECASE)
    if match:
        return float((match.group(1) or match.group(2)).replace(",", "."))
    return default


def check_file(path: str | Path, thickness: float, limits: dict | None = None) -> Sheet:
    limits = {**RECOMMENDED_LIMITS, **(limits or {})}
    path = Path(path)
    sheet = Sheet(path.name, thickness)
    try:
        loops = read_loops(path)
    except Exception as exc:
        sheet.error = f"не читается: {exc}"
        return sheet
    # a line drawn there and back makes a «loop» of no area: not a cut-out
    loops = [l for l in loops if l.area > 0.5 and min(l.box[2] - l.box[0], l.box[3] - l.box[1]) > 0.05]
    if not loops:
        sheet.error = "нет замкнутого контура"
        return sheet
    outer = max(loops, key=lambda l: l.area)
    inner = [l for l in loops if l is not outer]
    sheet.holes = len(inner)
    bends_file = path.parent / BENDS_SUBDIR / path.name
    if bends_file.is_file():
        try:
            lines, bend_outer = read_bends(bends_file)
            sheet.bends = align_bends(lines, bend_outer, outer) if bend_outer is not None else None
        except Exception:  # an unreadable bends file: the DXF is still checked
            sheet.bends = None
    if thickness <= 0:
        sheet.error = "толщина неизвестна — проверки пропущены"
        return sheet
    t = thickness

    def add(what, size, limit, where):
        sheet.problems.append(Problem(path.name, t, what, size, limit, where))

    for hole in inner:
        if hole.circle and hole.circle < limits["hole"] * t - 1e-6:
            add("Отверстие меньше толщины", hole.circle, limits["hole"] * t, hole.describe())
        if not hole.circle:
            x0, y0, x1, y1 = hole.box
            width = min(x1 - x0, y1 - y0)
            if width < limits["slot"] * t - 1e-6:
                add("Узкий вырез", width, limits["slot"] * t, hole.describe())
        web = gap(hole, outer)
        if web < limits["edge"] * t - 1e-6:
            add("Узкая перемычка до края", web, limits["edge"] * t, hole.describe())
    for i, a in enumerate(inner):
        for b in inner[i + 1:]:
            ax0, ay0, ax1, ay1 = a.box
            bx0, by0, bx1, by1 = b.box
            near = limits["between"] * t
            if ax0 - bx1 > near or bx0 - ax1 > near or ay0 - by1 > near or by0 - ay1 > near:
                continue
            web = gap(a, b)
            if web < near - 1e-6:
                add("Узкая перемычка между отверстиями", web, near, f"{a.describe()} и {b.describe()}")
    for bend in sheet.bends or []:
        to_bend = limits["bend_hole"] * t + t  # k·t + R, R = t: the bend radius is not in the DXF
        for hole in inner:
            distance = _to_bend(hole, bend)
            if distance <= 1e-6:
                add("Линия гиба проходит через отверстие", 0.0, to_bend, hole.describe())
            elif distance < to_bend - 1e-6:
                add("Отверстие у гиба", distance, to_bend, hole.describe())
        shortest = limits["flange"] * t
        for flange in _flanges(bend, sheet.bends, outer):
            if flange < shortest - 1e-6:
                add("Короткая полка", flange, shortest, "у линии гиба")
    merged: dict[tuple, Problem] = {}  # identical holes with the same remark: one line «×3»
    for problem in sheet.problems:
        key = (problem.what, problem.where, round(problem.size, 1))
        if key in merged:
            merged[key].count += 1
        else:
            merged[key] = problem
    sheet.problems = list(merged.values())
    return sheet


def check_folder(folder: str | Path, default_thickness: float = 0.0, limits: dict | None = None) -> list[Sheet]:
    """Every DXF under *folder* (the thickness folders of a laser package too)."""
    folder = Path(folder)
    return [check_file(p, thickness_of(p, default_thickness), limits) for p in sorted(folder.rglob("*.dxf"))
            if BENDS_SUBDIR not in p.relative_to(folder).parts]


def summary_text(sheets: list[Sheet]) -> str:
    problems = [p for s in sheets for p in s.problems for _ in range(p.count)]
    unread = [s for s in sheets if s.error]
    text = f"Проверка развёрток: файлов {len(sheets)}, замечаний {len(problems)}"
    if problems:
        kinds: dict[str, int] = {}
        for p in problems:
            kinds[p.what] = kinds.get(p.what, 0) + 1
        text += " (" + "; ".join(f"{k.lower()} — {v}" for k, v in kinds.items()) + ")"
    if unread:
        text += f"; не проверено {len(unread)}"
    return text + ". " + bends_note(sheets)


def bends_note(sheets: list[Sheet]) -> str:
    known = sum(1 for s in sheets if s.bends)
    if not known:
        return ("Отверстия у линий гиба и короткие полки не проверены: линий гиба нет (их сохраняет "
                f"«Пакет на лазер» в папку «{BENDS_SUBDIR}»).")
    return (f"Отверстия у гиба и короткие полки проверены у развёрток с линиями гиба: {known}; "
            "радиус гиба принят равным толщине.")


def save_xlsx(sheets: list[Sheet], path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="DDE4EE")
    wb = Workbook()
    ws = wb.active
    ws.title = "Замечания"
    ws.append(["Файл", "Толщина, мм", "Что", "Где", "Сколько", "Размер, мм", "Порог, мм"])
    for cell in ws[1]:
        cell.font, cell.fill = bold, head
    for s in sheets:
        for p in s.problems:
            ws.append([p.file, p.thickness, p.what, p.where, p.count, round(p.size, 2), round(p.limit, 2)])
    if ws.max_row == 1:
        ws.append(["Замечаний нет"])
    for col, width in zip("ABCDEFG", (52, 12, 36, 34, 10, 12, 12)):
        ws.column_dimensions[col].width = width
    ws = wb.create_sheet("Файлы")
    ws.append(["Файл", "Толщина, мм", "Отверстий и вырезов", "Линий гиба", "Замечаний", "Примечание"])
    for cell in ws[1]:
        cell.font, cell.fill = bold, head
    for s in sheets:
        ws.append([s.file, s.thickness or None, s.holes, len(s.bends) if s.bends is not None else None,
                   len(s.problems), s.error])
    for col, width in zip("ABCDEF", (52, 12, 20, 12, 12, 50)):
        ws.column_dimensions[col].width = width
    ws.append([])
    ws.append([bends_note(sheets)])
    path = Path(path)
    wb.save(path)
    return path
