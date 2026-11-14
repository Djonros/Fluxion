"""The cutting path of a nested sheet: what a plasma or a laser goes along.

For every detail: the holes first (counter-clockwise), the outer contour last (clockwise,
the good side of a plasma cut on the right), each shifted by half the kerf to the scrap
side. A contour starts with a lead-in and ends with an overcut:

- outer contour — in a convex corner (the one nearest the bottom right of the detail as it
  is drawn, so it turns with the detail, as ProNest does), the
  lead-in along the first edge from outside, the overcut along the last edge past the
  corner (as ProNest makes it); a round detail without corners — a straight lead-in from
  outside, square to the contour;
- a hole — a straight lead-in from inside, square to the middle of its longest edge
  (from the centre of a small round hole).

Bridges (microjoints): the outer contour is left uncut over *width* mm at *count*
places on its longest edges, so the detail stays in the sheet.

The details go nearest first from the origin; the cut-off line of a new remnant last.
The contours are polylines; ``fit_arcs`` turns runs of points on one circle into arcs
for the post-processor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .engine import NestedSheet


@dataclass
class CutSettings:
    kerf: float = 0.0  # mm, the width of the cut; 0 — from the technology of the machine
    lead_in: float = 0.0  # mm; 0 — from the technology of the machine (5 without one)
    overcut: float = 0.0  # mm past the corner; 0 — from the technology (1.65 without one)
    corner_angle: float = 150.0  # a corner sharper than this (inside angle, °) takes the lead-in
    bridges: int = 0  # on the outer contour of a detail; 0 — none
    bridge_width: float = 1.0  # mm
    bridge_min_size: float = 0.0  # mm: bridges only on details whose length is at least this
    small_hole: float = 0.0  # mm: holes smaller than this diameter are not cut (0 — all are)
    feed: float = 0.0  # mm/min; 0 — from the technology
    rapid: float = 15000.0  # mm/min, for the time
    pierce_time: float = 0.5  # s, for the time

    @property
    def lead(self) -> float:
        return self.lead_in or 5.0

    @property
    def over(self) -> float:
        return self.overcut or 1.65


@dataclass
class Move:
    kind: str  # "line" | "arc"
    end: tuple[float, float]
    center: tuple[float, float] | None = None  # arc
    cw: bool = False  # arc


@dataclass
class Cut:
    """One piercing of the torch: from *start* along *moves* (lead-in, contour, overcut)."""
    start: tuple[float, float]
    moves: list[Move]
    name: str = ""  # the detail, for the comment
    kind: str = "outer"  # outer | hole | crop
    lead_length: float = 0.0  # mm of the lead-in at the start

    @property
    def length(self) -> float:
        total, p = 0.0, self.start
        for m in self.moves:
            total += _move_length(p, m)
            p = m.end
        return total


@dataclass
class SheetPath:
    cuts: list[Cut] = field(default_factory=list)
    skipped_holes: int = 0

    @property
    def cut_length(self) -> float:
        return sum(c.length for c in self.cuts)

    @property
    def pierces(self) -> int:
        return len(self.cuts)

    def rapid_length(self) -> float:
        total, p = 0.0, (0.0, 0.0)
        for c in self.cuts:
            total += math.dist(p, c.start)
            p = c.moves[-1].end if c.moves else c.start
        return total

    def seconds(self, feed: float, settings: CutSettings) -> float:
        cut = self.cut_length / feed * 60 if feed else 0.0
        return cut + self.rapid_length() / settings.rapid * 60 + self.pierces * settings.pierce_time


# ── geometry ─────────────────────────────────────────────────────────────────


def _move_length(p, m: Move) -> float:
    if m.kind == "line":
        return math.dist(p, m.end)
    r = math.dist(m.center, m.end)
    return r * _sweep(p, m)


def _sweep(p, m: Move) -> float:
    a0 = math.atan2(p[1] - m.center[1], p[0] - m.center[0])
    a1 = math.atan2(m.end[1] - m.center[1], m.end[0] - m.center[0])
    d = (a0 - a1) if m.cw else (a1 - a0)
    d %= 2 * math.pi
    return d if d > 1e-9 else 2 * math.pi


def signed_area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum() / 2)


def clean(points: np.ndarray, tol: float = 1e-4) -> np.ndarray:
    """Without repeated points and points on a straight line between their neighbours."""
    pts = [p for i, p in enumerate(points) if np.hypot(*(p - points[i - 1])) > tol]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            ab, bc = b - a, c - b
            cross = ab[0] * bc[1] - ab[1] * bc[0]
            if abs(cross) <= tol * max(np.hypot(*ab), np.hypot(*bc)) * 1e-2 and float(ab @ bc) > 0:
                del pts[i]
                changed = True
                break
    return np.array(pts)


def oriented(points: np.ndarray, clockwise: bool) -> np.ndarray:
    return points[::-1].copy() if (signed_area(points) < 0) != clockwise else points.copy()


def offset(points: np.ndarray, distance: float) -> np.ndarray:
    """The polygon moved by *distance* to the left of its direction (a mitre at every corner;
    a corner sharper than ~15° is cut flat). For a clockwise outer contour the left is the
    outside; for a counter-clockwise hole — the inside of the hole."""
    if distance == 0:
        return points.copy()
    n = len(points)
    out = []
    for i in range(n):
        p0, p1, p2 = points[i - 1], points[i], points[(i + 1) % n]
        d0 = p1 - p0
        d1 = p2 - p1
        d0 = d0 / (np.hypot(*d0) or 1)
        d1 = d1 / (np.hypot(*d1) or 1)
        n0 = np.array([-d0[1], d0[0]])
        n1 = np.array([-d1[1], d1[0]])
        bis = n0 + n1
        norm = np.hypot(*bis)
        cos_half = norm / 2
        if norm < 1e-9 or cos_half < 0.13:  # a hairpin: two points instead of one
            out.append(p1 + n0 * distance)
            out.append(p1 + n1 * distance)
            continue
        out.append(p1 + bis / norm * distance / cos_half)
    return np.array(out)


def _convex(points: np.ndarray, i: int, clockwise: bool) -> float:
    """The inside angle at vertex i (°) when the corner is convex, else 360."""
    p0, p1, p2 = points[i - 1], points[i], points[(i + 1) % len(points)]
    a, b = p0 - p1, p2 - p1
    cross = (p1 - p0)[0] * (p2 - p1)[1] - (p1 - p0)[1] * (p2 - p1)[0]
    if (cross < 0) != clockwise or abs(cross) < 1e-9:
        return 360.0
    cos = float(a @ b) / ((np.hypot(*a) * np.hypot(*b)) or 1)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


# ── one contour ──────────────────────────────────────────────────────────────


def _rolled(points: np.ndarray, start: int) -> list[tuple[float, float]]:
    return [tuple(map(float, p)) for p in np.roll(points, -start, axis=0)]


def outer_cut(points: np.ndarray, s: CutSettings, name: str = "", angle: float = 0.0) -> Cut:
    """A clockwise outer contour (already shifted by the kerf): lead-in in a corner — the one
    nearest the bottom right of the detail turned back by *angle* (its turn on the sheet)."""
    from .engine import rotate

    path = clean(points)
    n = len(path)
    corners = [i for i in range(n) if _convex(path, i, True) <= s.corner_angle]
    if corners:
        own = rotate(path, -angle) if angle else path
        c = max(corners, key=lambda i: (round(own[i][0] - own[i][1], 3), -i))
        loop = _rolled(path, c)
        corner, nxt, prev = loop[0], loop[1], loop[-1]
        d1 = np.subtract(nxt, corner)
        edge = float(np.hypot(*d1))
        d1 = d1 / (edge or 1)
        inside = min(s.kerf / 2, edge / 2)
        entry = (corner[0] + d1[0] * inside, corner[1] + d1[1] * inside)
        start = (corner[0] - d1[0] * (s.lead - inside), corner[1] - d1[1] * (s.lead - inside))
        dn = np.subtract(corner, prev)
        dn = dn / (np.hypot(*dn) or 1)
        end = (corner[0] + dn[0] * s.over, corner[1] + dn[1] * s.over)
        last = float(np.hypot(*np.subtract(corner, prev)))
        back = min(inside, last / 2)
        before = (corner[0] - dn[0] * back, corner[1] - dn[1] * back)
        moves = [Move("line", entry)] + [Move("line", p) for p in loop[1:]] + [Move("line", before), Move("line", end)]
        return Cut(start, moves, name, "outer", s.lead)
    return _square_lead(path, s, name, "outer")


def _square_lead(path: np.ndarray, s: CutSettings, name: str, kind: str) -> Cut:
    """Lead-in square to the middle of the longest edge, from the left of the direction:
    the scrap side of a clockwise outer contour and of a counter-clockwise hole alike."""
    n = len(path)
    lengths = [math.dist(path[i], path[(i + 1) % n]) for i in range(n)]
    i = int(np.argmax(lengths))
    a, b = path[i], path[(i + 1) % n]
    mid = (a + b) / 2
    d = (b - a) / (lengths[i] or 1)
    left = np.array([-d[1], d[0]])
    lead = s.lead
    if kind == "hole":  # not past the far side of a small hole
        lead = min(lead, _span(path, mid, left) * 0.45)
    start = mid + left * lead
    loop = [tuple(map(float, mid))] + _rolled(path, i + 1) + [tuple(map(float, a))]
    moves = [Move("line", p) for p in loop]
    # the overcut: on along the first edge
    run = s.over
    end = mid + d * min(run, lengths[i] / 2)
    moves.append(Move("line", tuple(map(float, mid))))
    moves.append(Move("line", tuple(map(float, end))))
    return Cut(tuple(map(float, start)), moves, name, kind, lead)


def _span(path: np.ndarray, point: np.ndarray, direction: np.ndarray) -> float:
    """How far from *point* along *direction* the contour is crossed again."""
    best = math.inf
    n = len(path)
    for i in range(n):
        a, b = path[i], path[(i + 1) % n]
        e = b - a
        den = direction[0] * e[1] - direction[1] * e[0]
        if abs(den) < 1e-12:
            continue
        w = a - point
        t = (w[0] * e[1] - w[1] * e[0]) / den
        u = (w[0] * direction[1] - w[1] * direction[0]) / den
        if t > 1e-6 and -1e-9 <= u <= 1 + 1e-9:
            best = min(best, t)
    return best if best < math.inf else 0.0


def hole_cut(points: np.ndarray, s: CutSettings, name: str = "") -> Cut:
    """A counter-clockwise hole (already shifted by the kerf): lead-in from inside."""
    return _square_lead(clean(points), s, name, "hole")


def with_bridges(cut: Cut, s: CutSettings) -> list[Cut]:
    """The outer contour cut in pieces with *bridges* uncut gaps on its longest edges."""
    if s.bridges <= 0 or cut.kind != "outer":
        return [cut]
    points = [cut.start] + [m.end for m in cut.moves]
    edges = [(math.dist(points[k], points[k + 1]), k) for k in range(1, len(points) - 3)]
    need = s.bridge_width + 4 * s.kerf
    chosen = sorted(k for length, k in sorted(edges, reverse=True)[:s.bridges] if length >= need)
    if not chosen:
        return [cut]
    pieces, current, start = [], [], cut.start
    for k, m in enumerate(cut.moves, start=1):
        if k - 1 in chosen:  # the edge from points[k-1] to points[k]: a gap in its middle
            a, b = np.array(points[k - 1]), np.array(points[k])
            d = (b - a) / math.dist(a, b)
            mid = (a + b) / 2
            stop = tuple(map(float, mid - d * s.bridge_width / 2))
            go = tuple(map(float, mid + d * s.bridge_width / 2))
            current.append(Move("line", stop))
            pieces.append(Cut(start, current, cut.name, "outer", cut.lead_length if not pieces else 0.0))
            start, current = go, []
        current.append(m)
    pieces.append(Cut(start, current, cut.name, "outer", 0.0))
    return pieces


# ── a sheet ──────────────────────────────────────────────────────────────────


def sheet_path(sheet: NestedSheet, s: CutSettings, thickness: float = 0.0) -> SheetPath:
    """All the cuts of a sheet in their order."""
    result = SheetPath()
    half = s.kerf / 2
    details = []
    for p in sheet.placements:
        name = f"{p.part.item.designation} {p.part.item.name}".strip()
        outer = oriented(p.loops[0], clockwise=True)
        cuts = []
        for loop in p.loops[1:]:
            hole = oriented(loop, clockwise=False)
            x0, y0 = hole.min(axis=0)
            x1, y1 = hole.max(axis=0)
            if s.small_hole and max(x1 - x0, y1 - y0) < s.small_hole:
                result.skipped_holes += 1
                continue
            cuts.append(hole_cut(offset(hole, half), s, name))
        size = max(p.box[2] - p.box[0], p.box[3] - p.box[1])
        main = outer_cut(offset(outer, half), s, name, p.angle)
        if s.bridges and size >= s.bridge_min_size:
            cuts += with_bridges(main, s)
        else:
            cuts.append(main)
        details.append(cuts)
    position = (0.0, 0.0)
    while details:  # the nearest detail next
        k = min(range(len(details)), key=lambda i: math.dist(position, details[i][0].start))
        cuts = details.pop(k)
        result.cuts += cuts
        last = cuts[-1]
        position = last.moves[-1].end if last.moves else last.start
    if sheet.remnant and sheet.cut_x:
        x = sheet.cut_x - half
        result.cuts.append(Cut((x, 0.0), [Move("line", (x, sheet.width))], "CROP", "crop"))
    return result


# ── arcs ─────────────────────────────────────────────────────────────────────


def _circle(a, b, c):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def fit_arcs(start, moves: list[Move], tolerance: float = 0.02, min_points: int = 4,
             max_radius: float = 5000.0) -> list[Move]:
    """Lines whose points lie on one circle (within *tolerance* mm), turning one way and
    less than 180° each, become arcs."""
    points = [start] + [m.end for m in moves]
    out: list[Move] = []
    i = 0
    n = len(points)
    while i < n - 1:
        best = None
        j = i + min_points - 1
        while j < n:
            run = points[i:j + 1]
            fit = _circle(run[0], run[len(run) // 2], run[-1])
            if fit is None:
                break
            center, r = fit
            if r > max_radius or any(abs(math.hypot(p[0] - center[0], p[1] - center[1]) - r) > tolerance for p in run):
                break
            crosses = [(q[0] - p[0]) * (o[1] - q[1]) - (q[1] - p[1]) * (o[0] - q[0])
                       for p, q, o in zip(run, run[1:], run[2:])]
            if not (all(c > 0 for c in crosses) or all(c < 0 for c in crosses)):
                break
            seg = max(math.dist(p, q) for p, q in zip(run, run[1:]))
            if seg > r:  # long chords: lines that only happen to touch a circle
                break
            cw = crosses[0] < 0
            if _sweep(run[0], Move("arc", run[-1], center, cw)) > math.pi:
                break
            best = (j, center, cw)
            j += 1
        if best:
            j, center, cw = best
            out.append(Move("arc", points[j], center, cw))
            i = j
        else:
            out.append(Move("line", points[i + 1]))
            i += 1
    return out
