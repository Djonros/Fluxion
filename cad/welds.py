"""Welds of the welded assemblies of a product, found from the joints of their details.

A welded assembly is one without fasteners among its components. Two of its
details are joined where a flat face of one lies on a flat face of the other:
the faces are parallel, in one plane and overlap. The seam goes around the
common area (a rib on a wall gets a fillet seam on both sides, a pad gets one
all around), so its length is the perimeter of that area. The leg of the seam
is the smaller thickness of the two details; the deposited metal is a
triangle k²/2 along the seam times WELD_FACTOR (reinforcement and spatter).

The faces are read through the API5 of KOMPAS-3D in the coordinates of each
detail and moved into the assembly by its placement matrix: the intersection
check of KOMPAS itself works in the coordinates of the details and is of no use
in an assembly.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .assembly import ASSEMBLY, DETAIL, STANDARD, Component, material_parts, no_material

STEEL_DENSITY = 7.85e-3  # g/mm³
WELD_FACTOR = 1.2  # deposited metal over the bare triangle: reinforcement, spatter
MAX_LEG = 12.0  # mm: the leg follows the thinner detail but is never bigger
TOUCH = 0.2  # mm: faces closer than this lie in one plane
MIN_AREA = 1.0  # mm²: a smaller common area is a touch along an edge, not a joint


# ── which assemblies are welded, how thick a detail is ───────────────────────


AXLE_RE = re.compile(r"^\s*(полу)?ось\b", re.IGNORECASE)  # bought axles and half-axles
INSTALLATION_RE = re.compile(r"^\s*(установка|монтаж|комплект)\b", re.IGNORECASE)


def is_welded(assembly: Component) -> bool:
    """No fasteners among the components and at least two details. An installation
    ("Установка настила": panels held by clips from «Прочие изделия») is not welded."""
    if INSTALLATION_RE.match(assembly.name or "") or AXLE_RE.match(assembly.name or ""):
        return False
    if any(c.kind == STANDARD for c in assembly.children):
        return False
    return sum(1 for c in assembly.children if welded_detail(c)) >= 2


def welded_detail(c: Component) -> bool:
    """A detail that takes part in the welds: one without a material (or with the
    default one of KOMPAS) is a placeholder or a bought part, not a welded detail."""
    return c.kind == DETAIL and not no_material(c.material)


def thickness_from_material(raw: str) -> float | None:
    """Thickness of the sortament: "Лист Б-ПУ-О-10 ГОСТ 19903" -> 10, "Лист 8 ГОСТ ..." -> 8,
    "120х120х8-ГОСТ 30245" -> 8, "Труба 57х3.5" -> 3.5; None for a plain grade."""
    sortament, grade = material_parts(raw)
    text = (sortament or grade or "").replace(",", ".")  # a profile is often written without a fraction
    m = re.search(r"лист\D*?(\d+(?:\.\d+)?)(?=\s|$|\s*гост)", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*[хx×]\s*(\d+(?:\.\d+)?)(?:\s*[хx×]\s*(\d+(?:\.\d+)?))?", text)
    if m:
        return float(m.group(3) or m.group(2))
    return None


# ── plane geometry ───────────────────────────────────────────────────────────

Vec = tuple[float, float, float]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a: Vec) -> Vec:
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def transform(matrix, p: Vec) -> Vec:
    """A point by a KOMPAS 4×4 matrix (row vector, translation in the last row)."""
    x, y, z = p
    m = matrix
    return (x * m[0] + y * m[4] + z * m[8] + m[12], x * m[1] + y * m[5] + z * m[9] + m[13],
            x * m[2] + y * m[6] + z * m[10] + m[14])


def rotate(matrix, v: Vec) -> Vec:
    x, y, z = v
    m = matrix
    return (x * m[0] + y * m[4] + z * m[8], x * m[1] + y * m[5] + z * m[9], x * m[2] + y * m[6] + z * m[10])


@dataclass
class Face:
    normal: Vec  # unit, either direction
    points: list[Vec]  # outer contour, assembly coordinates


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convex hull, counter-clockwise (monotone chain)."""
    pts = sorted(set((round(x, 4), round(y, 4)) for x, y in points))
    if len(pts) < 3:
        return pts

    def half(seq):
        h: list = []
        for p in seq:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (p[1] - h[-2][1])
                                   - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(p)
        return h

    return half(pts)[:-1] + half(list(reversed(pts)))[:-1]


def _clip(subject, clipper):
    """Sutherland–Hodgman: convex *subject* ∩ convex *clipper* (counter-clockwise)."""
    out = list(subject)
    for i in range(len(clipper)):
        if not out:
            break
        a, b = clipper[i], clipper[(i + 1) % len(clipper)]

        def inside(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= -1e-9

        def cut(p, q):
            den = (p[0] - q[0]) * (a[1] - b[1]) - (p[1] - q[1]) * (a[0] - b[0])
            if abs(den) < 1e-12:
                return q
            t = ((p[0] - a[0]) * (a[1] - b[1]) - (p[1] - a[1]) * (a[0] - b[0])) / den
            return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))

        inp, out = out, []
        s = inp[-1]
        for e in inp:
            if inside(e):
                if not inside(s):
                    out.append(cut(s, e))
                out.append(e)
            elif inside(s):
                out.append(cut(s, e))
            s = e
    return out


def _area(poly) -> float:
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


def _perimeter(poly) -> float:
    return sum(math.dist(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def _bbox(faces: list[Face]) -> tuple[Vec, Vec] | None:
    points = [p for f in faces for p in f.points]
    if not points:
        return None
    return (tuple(min(p[k] for p in points) for k in range(3)), tuple(max(p[k] for p in points) for k in range(3)))


def _boxes_touch(a, b) -> bool:
    if a is None or b is None:
        return False
    return all(a[0][k] <= b[1][k] + TOUCH and b[0][k] <= a[1][k] + TOUCH for k in range(3))


def joint_length(a: list[Face], b: list[Face]) -> float:
    """Length of the seam between two details: perimeters of the common areas of their faces."""
    return joint_contact(a, b)[0]


def joint_contact(a: list[Face], b: list[Face]) -> tuple[float, float]:
    """(seam length, common area in mm²) of two details; the area is hidden from the paint."""
    if not _boxes_touch(_bbox(a), _bbox(b)):
        return 0.0, 0.0  # far apart: most pairs of a big assembly end here
    total = 0.0
    surface = 0.0
    for fa in a:
        for fb in b:
            if abs(_dot(fa.normal, fb.normal)) < 0.999:
                continue  # not parallel
            o = fa.points[0]
            if abs(_dot(_sub(fb.points[0], o), fa.normal)) > TOUCH:
                continue  # parallel planes apart
            ex = _unit(_sub(fa.points[1], o))
            ey = _cross(fa.normal, ex)
            flat = lambda pts: _hull([(_dot(_sub(p, o), ex), _dot(_sub(p, o), ey)) for p in pts])  # noqa: E731
            pa, pb = flat(fa.points), flat(fb.points)
            if len(pa) < 3 or len(pb) < 3:
                continue
            common = _clip(pa, pb)
            if len(common) >= 3 and _area(common) > MIN_AREA:
                total += _perimeter(common)
                surface += _area(common)
    return total, surface


def leg_for(thickness_a: float | None, thickness_b: float | None) -> float:
    """Leg of a fillet seam: the smaller thickness of the two details, at most MAX_LEG."""
    known = [x for x in (thickness_a, thickness_b) if x]
    return round(min(min(known), MAX_LEG), 1) if known else 0.0


def weld_mass_g(leg_mm: float, length_mm: float, factor: float = WELD_FACTOR) -> float:
    return leg_mm * leg_mm / 2 * length_mm * STEEL_DENSITY * factor


# ── results ──────────────────────────────────────────────────────────────────


@dataclass
class Joint:
    a: str  # titles of the two details
    b: str
    leg_mm: float
    length_mm: float
    count: int = 1  # identical joints in the assembly
    area_mm2: float = 0.0  # common area of the two details (not painted)

    @property
    def mass_g(self) -> float:
        return weld_mass_g(self.leg_mm, self.length_mm * self.count)


@dataclass
class WeldedAssembly:
    assembly: Component
    qty_product: int
    joints: list[Joint] = field(default_factory=list)
    note: str = ""  # why it was not computed

    @property
    def length_mm(self) -> float:
        return sum(j.length_mm * j.count for j in self.joints)

    @property
    def mass_g(self) -> float:
        return sum(j.mass_g for j in self.joints)


def welded_assemblies(top: Component) -> list[tuple[Component, int]]:
    """Welded assemblies of the product, each once, with the quantity on the product."""
    found: dict[tuple, list] = {}

    def walk(assembly: Component, mult: int) -> None:
        groups: dict[tuple, list] = {}
        for c in assembly.children:
            groups.setdefault(c.key, [c, 0])[1] += 1
        for child, n in groups.values():
            if child.kind != ASSEMBLY:
                continue
            if is_welded(child):
                found.setdefault(child.key, [child, 0])[1] += n * mult
            walk(child, n * mult)

    if is_welded(top):
        found[top.key] = [top, 1]
    walk(top, 1)
    return [(c, n) for c, n in found.values()]


def group_joints(raw: list[tuple]) -> list[Joint]:
    """Identical joints (same details, leg and length) as one line with a count.
    A raw joint is (a, b, leg, length) or (a, b, leg, length, area)."""
    out: dict[tuple, Joint] = {}
    for a, b, leg, length, *rest in raw:
        a, b = sorted((a, b))
        key = (a, b, round(leg, 1), round(length))
        if key in out:
            out[key].count += 1
        else:
            out[key] = Joint(a, b, leg, length, area_mm2=rest[0] if rest else 0.0)
    return list(out.values())


# ── reading from KOMPAS ──────────────────────────────────────────────────────


def _api5(app7):
    import pythoncom
    from win32com.client import Dispatch, gencache

    api5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
    k5 = Dispatch("Kompas.Application.5")
    kompas = api5.KompasObject(k5._oleobj_.QueryInterface(api5.KompasObject.CLSID, pythoncom.IID_IDispatch))
    return api5, kompas


def _placement(api5, part5):
    _ok, matrix = api5.ksPlacement(part5.GetPlacement()).GetMatrix3D()
    return matrix


def placed(faces: list[Face], matrix) -> list[Face]:
    """Faces of a detail (its own coordinates) moved into the assembly."""
    return [Face(_unit(rotate(matrix, f.normal)), [transform(matrix, q) for q in f.points]) for f in faces]


def _detail_faces(api5, part5) -> tuple[list[Face], float | None]:
    """Flat faces of a detail in its own coordinates and its smallest overall size.
    The COM calls are slow: the caller keeps the result per detail file and moves a
    copy of it into the assembly for every instance (a grating has hundreds of bars)."""
    matrix = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    body = part5.GetMainBody()
    if body is None:
        return [], None
    body = api5.ksBody(body)
    gab = body.GetGabarit()
    smallest = min(abs(gab[4] - gab[1]), abs(gab[5] - gab[2]), abs(gab[6] - gab[3])) if gab and gab[0] else None
    faces_out = []
    faces = api5.ksFaceCollection(body.FaceCollection())
    for i in range(faces.GetCount()):
        f = api5.ksFaceDefinition(faces.GetByIndex(i))
        if not f.IsPlanar():
            continue
        loops = api5.ksLoopCollection(f.LoopCollection())
        outer = None
        for k in range(loops.GetCount()):
            loop = api5.ksLoop(loops.GetByIndex(k))
            if loop.IsOuter():
                outer = loop
                break
        if outer is None:
            continue
        points: list[Vec] = []
        edges = api5.ksEdgeCollection(outer.EdgeCollection())
        for k in range(edges.GetCount()):
            e = api5.ksEdgeDefinition(edges.GetByIndex(k))
            for start in (True, False):
                v = e.GetVertex(start)
                if v is None:
                    continue
                _ok, x, y, z = api5.ksVertexDefinition(v).GetPoint()
                p = transform(matrix, (x, y, z))
                if not any(abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) < 1e-6 for q in points):
                    points.append(p)
        if len(points) < 3:
            continue
        surf = api5.ksSurface(f.GetSurface())
        u = (surf.GetParamUMin() + surf.GetParamUMax()) / 2
        w = (surf.GetParamVMin() + surf.GetParamVMax()) / 2
        _ok, nx, ny, nz = surf.GetNormal(u, w)
        faces_out.append(Face(_unit(rotate(matrix, (nx, ny, nz))), points))
    return faces_out, smallest


def _title(part5) -> str:
    marking = str(getattr(part5, "marking", "") or "").strip()
    name = str(getattr(part5, "name", "") or "").strip()
    return " ".join(x for x in (marking, name) if x) or "(без наименования)"


def compute(top: Component, log=print) -> list[WeldedAssembly]:
    """Welds of every welded assembly of the product. The product is opened once,
    read-only; its tree is walked through API7 (local subassemblies included) and
    each detail is handed to API5 for its faces. Assemblies without fasteners in
    which no joint was found (installations of bought units) are left out."""
    from win32com.client import CastTo, Dispatch

    from .assembly import opened

    if not top.file or ">" in top.file or not Path(top.file).is_file():
        raise RuntimeError(f"файл изделия не найден: {top.file}")
    app = Dispatch("Kompas.Application.7")
    api5, kompas = _api5(app)
    wanted = {c.key: qty for c, qty in welded_assemblies(top)}
    results: dict[tuple, WeldedAssembly] = {}
    shapes: dict[str, tuple[list[Face], float | None]] = {}

    def details_of(part7, assembly: Component) -> list:
        """(title, faces, thickness) of the details of *assembly* (its IPart7 is *part7*).
        The overall boxes KOMPAS gives for components of nested subassemblies do not
        always agree with their geometry, so every detail is read: no joint is lost."""
        out = []
        parts = [CastTo(p, "IPart7") for p in (part7.Parts or [])]
        parts = [p for p in parts if not getattr(p, "Excluded", False)]
        for child, p7 in zip(assembly.children, parts):
            if not welded_detail(child):
                continue
            p5 = api5.ksPart(kompas.TransferInterface(p7, 1, 0))
            # the geometry of a detail file is read once; local details (">" in the file) each time
            cache_key = child.file.lower() if child.file and ">" not in child.file else None
            if cache_key is not None and cache_key in shapes:
                faces, smallest = shapes[cache_key]
            else:
                faces, smallest = _detail_faces(api5, p5)
                if cache_key is not None:
                    shapes[cache_key] = (faces, smallest)
            out.append((child.title, placed(faces, _placement(api5, p5)),
                        thickness_from_material(child.material) or smallest))
        return out

    def walk(part7, assembly: Component) -> None:
        if assembly.key in wanted and assembly.key not in results:
            started = time.monotonic()
            result = WeldedAssembly(assembly, wanted[assembly.key])
            try:
                details = details_of(part7, assembly)
            except Exception as exc:
                result.note = f"КОМПАС не отдал геометрию: {exc}"
                details = []
            raw = []
            boxes = [_bbox(d[1]) for d in details]
            for i in range(len(details)):
                for j in range(i + 1, len(details)):
                    if not _boxes_touch(boxes[i], boxes[j]):
                        continue
                    length, area = joint_contact(details[i][1], details[j][1])
                    if length > 0:
                        raw.append((details[i][0], details[j][0], leg_for(details[i][2], details[j][2]), length, area))
            result.joints = group_joints(raw)
            results[assembly.key] = result
            log(f"Сварка: {assembly.title} — деталей {len(details)}, стыков {len(raw)}, "
                f"{time.monotonic() - started:.1f} с")
        parts = [CastTo(p, "IPart7") for p in (part7.Parts or [])]
        parts = [p for p in parts if not getattr(p, "Excluded", False)]
        seen = set()
        for child, p7 in zip(assembly.children, parts):
            if child.kind == ASSEMBLY and child.key not in seen:
                seen.add(child.key)
                walk(p7, child)

    with opened(app, top.file) as doc:
        walk(CastTo(CastTo(doc, "IKompasDocument3D").TopPart, "IPart7"), top)
    return [r for r in results.values() if r.joints or r.note]
