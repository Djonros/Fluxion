"""A clean DXF of a flat pattern for laser cutting.

KOMPAS saves a drawing to DXF with its frame and title block (blocks inserted
in the model space).  This module reads the geometry of that DXF, keeps the
lines, arcs and circles, sorts them by line type — the solid contour and holes
go to layer CUT, the bend lines (dash-dot) to layer BEND — moves the pattern to
the origin and writes a plain R12 DXF that any laser CAM opens.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

CUT, BEND = "CUT", "BEND"


@dataclass
class Entity:
    kind: str           # LINE | ARC | CIRCLE
    layer: str          # CUT | BEND
    data: tuple         # LINE: x1, y1, x2, y2; ARC: cx, cy, r, a1, a2 (degrees, CCW); CIRCLE: cx, cy, r


@dataclass
class FlatPattern:
    entities: list[Entity] = field(default_factory=list)

    def of(self, layer: str, kind: str | None = None) -> list[Entity]:
        return [e for e in self.entities if e.layer == layer and (kind is None or e.kind == kind)]

    @property
    def bend_lines(self) -> int:
        return len(self.of(BEND))

    @property
    def holes(self) -> int:
        return len(self.of(CUT, "CIRCLE"))

    def extents(self) -> tuple[float, float, float, float]:
        xs, ys = [], []
        for e in self.of(CUT) or self.entities:
            for x, y in _points(e):
                xs.append(x)
                ys.append(y)
        return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)

    @property
    def size(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.extents()
        return x2 - x1, y2 - y1


def _points(e: Entity) -> list[tuple[float, float]]:
    if e.kind == "LINE":
        x1, y1, x2, y2 = e.data
        return [(x1, y1), (x2, y2)]
    cx, cy, r = e.data[:3]
    if e.kind == "CIRCLE":
        return [(cx - r, cy - r), (cx + r, cy + r)]
    a1, a2 = e.data[3], e.data[4]
    span = (a2 - a1) % 360 or 360
    angles = [a1, a1 + span] + [q for q in range(0, 720, 90) if a1 < q < a1 + span]
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in angles]


def _pairs(path: Path) -> list[tuple[str, str]]:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    lines = [line.strip() for line in text.splitlines()]
    return list(zip(lines[0::2], lines[1::2]))


def _is_bend(linetype: str) -> bool:
    linetype = linetype.upper()
    return any(word in linetype for word in ("CHAIN", "AXIS", "CENTER", "DASHDOT", "ОСЕВ", "ШТРИХПУНКТ"))


def read(path: Path) -> FlatPattern:
    """Lines, arcs and circles of the ENTITIES section; inserts (frame, title block) and texts are skipped."""
    pattern = FlatPattern()
    in_entities = False
    current: dict | None = None

    def flush() -> None:
        if not current or current["type"] not in ("LINE", "ARC", "CIRCLE"):
            return
        g = current.get
        try:
            if current["type"] == "LINE":
                data = (float(g("10")), float(g("20")), float(g("11")), float(g("21")))
            elif current["type"] == "ARC":
                data = (float(g("10")), float(g("20")), float(g("40")), float(g("50")), float(g("51")))
            else:
                data = (float(g("10")), float(g("20")), float(g("40")))
        except (TypeError, ValueError):
            return
        bend = (g("8") or "").upper() == BEND or _is_bend(g("6") or "")
        pattern.entities.append(Entity(current["type"], BEND if bend else CUT, data))

    for code, value in _pairs(path):
        if code == "2" and value == "ENTITIES" and current is None:
            in_entities = True
            continue
        if not in_entities:
            continue
        if code == "0":
            flush()
            if value == "ENDSEC":
                break
            current = {"type": value}
        elif current is not None and code not in current:
            current[code] = value
    return pattern


def _merge_lines(lines: list[Entity], tol: float = 1e-6) -> list[Entity]:
    """Join collinear lines that touch (KOMPAS splits the contour at the bend zones)."""
    segs = [list(e.data) for e in lines]
    merged = True
    while merged:
        merged = False
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                a, b = segs[i], segs[j]
                ends_a, ends_b = [(a[0], a[1]), (a[2], a[3])], [(b[0], b[1]), (b[2], b[3])]
                shared = [(p, q) for p in ends_a for q in ends_b if math.dist(p, q) < tol]
                if not shared:
                    continue
                da = (a[2] - a[0], a[3] - a[1])
                db = (b[2] - b[0], b[3] - b[1])
                if abs(da[0] * db[1] - da[1] * db[0]) > tol * max(1.0, math.hypot(*da) * math.hypot(*db)):
                    continue
                p = shared[0][0]
                far_a = ends_a[1] if math.dist(ends_a[0], p) < tol else ends_a[0]
                far_b = ends_b[1] if math.dist(ends_b[0], p) < tol else ends_b[0]
                segs[i] = [far_a[0], far_a[1], far_b[0], far_b[1]]
                del segs[j]
                merged = True
                break
            if merged:
                break
    return [Entity("LINE", lines[0].layer if lines else CUT, tuple(s)) for s in segs]


def clean(pattern: FlatPattern) -> FlatPattern:
    """Merged contour lines, everything moved so the contour starts at (0, 0)."""
    cut_lines = _merge_lines(pattern.of(CUT, "LINE"))
    others = [e for e in pattern.entities if not (e.layer == CUT and e.kind == "LINE")]
    result = FlatPattern(cut_lines + others)
    x0, y0, _, _ = result.extents()
    moved = []
    for e in result.entities:
        d = list(e.data)
        if e.kind == "LINE":
            d[0] -= x0; d[1] -= y0; d[2] -= x0; d[3] -= y0
        else:
            d[0] -= x0; d[1] -= y0
        moved.append(Entity(e.kind, e.layer, tuple(round(v, 6) for v in d)))
    return FlatPattern(moved)


def write(pattern: FlatPattern, path: Path) -> Path:
    """Plain R12 ASCII DXF, millimetres: layers CUT (white) and BEND (red)."""
    out: list[str] = []

    def put(*pairs) -> None:
        for code, value in zip(pairs[0::2], pairs[1::2]):
            out.append(str(code))
            out.append(f"{value:.6f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value))

    x1, y1, x2, y2 = pattern.extents()
    put(0, "SECTION", 2, "HEADER", 9, "$ACADVER", 1, "AC1009", 9, "$INSUNITS", 70, 4, 9, "$MEASUREMENT", 70, 1,
        9, "$EXTMIN", 10, x1, 20, y1, 30, 0.0, 9, "$EXTMAX", 10, x2, 20, y2, 30, 0.0, 0, "ENDSEC")
    put(0, "SECTION", 2, "TABLES",
        0, "TABLE", 2, "LTYPE", 70, 1,
        0, "LTYPE", 2, "CONTINUOUS", 70, 0, 3, "Solid line", 72, 65, 73, 0, 40, 0.0,
        0, "ENDTAB",
        0, "TABLE", 2, "LAYER", 70, 2,
        0, "LAYER", 2, CUT, 70, 0, 62, 7, 6, "CONTINUOUS",
        0, "LAYER", 2, BEND, 70, 0, 62, 1, 6, "CONTINUOUS",
        0, "ENDTAB", 0, "ENDSEC")
    put(0, "SECTION", 2, "ENTITIES")
    for e in pattern.entities:
        d = e.data
        if e.kind == "LINE":
            put(0, "LINE", 8, e.layer, 10, d[0], 20, d[1], 30, 0.0, 11, d[2], 21, d[3], 31, 0.0)
        elif e.kind == "ARC":
            put(0, "ARC", 8, e.layer, 10, d[0], 20, d[1], 30, 0.0, 40, d[2], 50, d[3], 51, d[4])
        else:
            put(0, "CIRCLE", 8, e.layer, 10, d[0], 20, d[1], 30, 0.0, 40, d[2])
    put(0, "ENDSEC", 0, "EOF")
    Path(path).write_text("\n".join(out) + "\n", encoding="ascii")
    return Path(path)


def convert(kompas_dxf: Path, target: Path) -> FlatPattern:
    """KOMPAS's DXF of a flat-pattern view -> clean DXF at *target*; the cleaned pattern."""
    pattern = clean(read(kompas_dxf))
    if not pattern.of(CUT):
        raise ValueError("В DXF КОМПАСа нет контура развёртки.")
    write(pattern, target)
    return pattern
