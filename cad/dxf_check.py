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
1·t. Bend lines are not in the DXF of the laser (they are removed with the
annotations), so «отверстие у гиба» and «короткая полка» are not checked here.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

JOIN_TOLERANCE = 0.2  # mm: the ends of two segments of one loop (splines of KOMPAS end a little apart)
FLATTEN = 0.1  # mm: the chord error of arcs and splines

RECOMMENDED_LIMITS = {"hole": 1.0, "edge": 1.0, "between": 1.0, "slot": 1.0}


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
    return [check_file(p, thickness_of(p, default_thickness), limits) for p in sorted(folder.rglob("*.dxf"))]


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
    return text + ". Отверстия у линий гиба и короткие полки по DXF не проверяются: линий гиба в нём нет."


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
    ws.append(["Файл", "Толщина, мм", "Отверстий и вырезов", "Замечаний", "Примечание"])
    for cell in ws[1]:
        cell.font, cell.fill = bold, head
    for s in sheets:
        ws.append([s.file, s.thickness or None, s.holes, len(s.problems), s.error])
    for col, width in zip("ABCDE", (52, 12, 20, 12, 50)):
        ws.column_dimensions[col].width = width
    ws.append([])
    ws.append(["Отверстия у линий гиба и короткие полки по DXF не проверяются: линии гиба вычищаются из развёртки "
               "для лазера."])
    path = Path(path)
    wb.save(path)
    return path
