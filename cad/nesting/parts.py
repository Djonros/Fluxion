"""The DXF of the details of an order and their shapes.

Where the DXF is: the path in the order, else a file in the given folders (the folder of
the order with its subfolders first) whose name starts with the designation —
«544000.2801121-14 - Ребро большое.dxf», as the laser package and ProNest keep them —
a file under a folder of the thickness («…\\8\\») first. The «_гибы» folders of the
laser package hold bend lines, not contours: they are skipped.

A shape is the outer contour of the flat pattern and its holes, in mm, from the loops
«Проверка развёрток» reads (cad/dxf_check.py).
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .orders import OrderItem

SKIP_DIRS = {"_гибы", "__pycache__", ".git"}


def designation_key(text: str) -> str:
    """«544000.2801121-14», «544000.2801121-14 - Ребро большое», «544000-2801121-14» → one key."""
    head = re.split(r"\s+[-–—]\s+|\s{2,}", str(text).strip(), maxsplit=1)[0]
    return re.sub(r"[\s.\-_–—]", "", head).lower().replace("ё", "е")


@dataclass
class DxfIndex:
    """The DXF files of some folders by the designation in their names."""
    files: dict[str, list[Path]] = field(default_factory=dict)
    count: int = 0

    @classmethod
    def build(cls, folders, limit: int = 200_000) -> DxfIndex:
        index = cls()
        seen: set[str] = set()
        for folder in folders:
            if not folder:
                continue
            folder = Path(folder)
            if not folder.is_dir():
                continue
            for root, dirs, names in os.walk(folder):
                dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
                for name in names:
                    if not name.lower().endswith(".dxf"):
                        continue
                    path = Path(root) / name
                    key = str(path).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    index.files.setdefault(designation_key(path.stem), []).append(path)
                    index.count += 1
                    if index.count >= limit:
                        return index
        return index

    def find(self, designation: str, thickness: float = 0.0) -> list[Path]:
        """The files of *designation*, the ones in a folder of *thickness* first."""
        found = list(self.files.get(designation_key(designation), []))

        def in_thickness(path: Path) -> bool:
            try:
                return thickness > 0 and abs(float(path.parent.name.replace(",", ".")) - thickness) < 1e-6
            except ValueError:
                return False

        return sorted(found, key=lambda p: (not in_thickness(p), len(str(p))))


@dataclass
class Shape:
    """A flat pattern in mm: loops as point lists, the outer one first."""
    loops: list[list[tuple[float, float]]]
    path: Path | None = None

    @property
    def outer(self) -> list[tuple[float, float]]:
        return self.loops[0]

    @property
    def holes(self) -> list[list[tuple[float, float]]]:
        return self.loops[1:]

    @property
    def box(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.outer]
        ys = [p[1] for p in self.outer]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def length(self) -> float:
        x0, y0, x1, y1 = self.box
        return max(x1 - x0, y1 - y0)

    @property
    def width(self) -> float:
        x0, y0, x1, y1 = self.box
        return min(x1 - x0, y1 - y0)

    @property
    def area(self) -> float:
        """Net area, mm²: the outer contour without the holes."""
        return max(0.0, polygon_area(self.outer) - sum(polygon_area(h) for h in self.holes))

    @property
    def cut_length(self) -> float:
        return sum(perimeter(loop) for loop in self.loops)

    @property
    def pierces(self) -> int:
        return len(self.loops)


def polygon_area(points) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))) / 2


def perimeter(points) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:] + points[:1]))


def _inside(point, polygon) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


class ShapeError(ValueError):
    pass


def shape_from_loops(loops: list[list[tuple[float, float]]], path: Path | None = None) -> Shape:
    """The biggest loop is the outer contour, the loops inside it are holes; the loops
    outside (a second detail, a frame) are an error: one file — one detail."""
    loops = [list(loop) for loop in loops if len(loop) >= 3 and polygon_area(loop) > 1e-6]
    if not loops:
        raise ShapeError("нет замкнутого контура")
    loops.sort(key=polygon_area, reverse=True)
    outer, holes, outside = loops[0], [], 0
    for loop in loops[1:]:
        if _inside(loop[0], outer):
            holes.append(loop)
        else:
            outside += 1
    if outside:
        raise ShapeError(f"в файле несколько контуров рядом ({outside + 1}) — нужна одна деталь на файл")
    # holes inside holes (an island in a cut-out) are scrap: they fall out with the hole
    holes = [h for h in holes if not any(h is not o and _inside(h[0], o) for o in holes)]
    return Shape([outer] + holes, path)


def read_shape(path: str | Path) -> Shape:
    from cad.dxf_check import read_loops

    path = Path(path)
    try:
        loops = read_loops(path)
    except Exception as exc:  # not a DXF, a broken file
        raise ShapeError(f"не читается: {exc}") from exc
    return shape_from_loops([loop.points for loop in loops], path)


@dataclass
class Part:
    """A detail of the order with its DXF and shape (or why there is none)."""
    item: OrderItem
    candidates: list[Path] = field(default_factory=list)
    shape: Shape | None = None
    error: str = ""

    @property
    def path(self) -> Path | None:
        return self.shape.path if self.shape else (self.candidates[0] if self.candidates else None)


def resolve(items: list[OrderItem], folders, base: Path | None = None, index: DxfIndex | None = None,
            archive_lookup=None) -> list[Part]:
    """The DXF and the shape of every item. *archive_lookup*(designation) → [paths] is asked
    when the folders have no file (the «Архив КД»)."""
    index = index or DxfIndex.build(folders)
    parts = []
    for item in items:
        part = Part(item)
        if item.dxf:
            given = Path(item.dxf)
            if not given.is_absolute() and base is not None:
                given = base / given
            part.candidates = [given] if given.is_file() else []
            if not part.candidates:
                part.error = f"файл из заявки не найден: {item.dxf}"
        if not part.candidates and not part.error:
            thickness = item.material.thickness if item.material else 0.0
            part.candidates = index.find(item.designation, thickness)
            if not part.candidates and archive_lookup is not None:
                try:
                    part.candidates = [Path(p) for p in archive_lookup(item.designation)]
                except Exception:  # the archive is not indexed or busy: not found
                    part.candidates = []
            if not part.candidates:
                part.error = "DXF не найден"
        if part.candidates and not part.error:
            try:
                part.shape = read_shape(part.candidates[0])
            except ShapeError as exc:
                part.error = str(exc)
        parts.append(part)
    return parts


def archive_lookup(data_dir: str | Path):
    """A lookup of the DXF of a designation in the index of the «Архив КД» (read only);
    None when the archive was never indexed."""
    import sqlite3

    from rag.archive import DB_NAME

    db_path = Path(data_dir) / DB_NAME
    if not db_path.is_file():
        return None

    def lookup(designation: str) -> list[Path]:
        key = designation_key(designation)
        head = re.split(r"\s+[-–—]\s+", designation.strip(), maxsplit=1)[0]
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT path FROM files WHERE lower(path) LIKE '%.dxf' AND path LIKE ?",
                              (f"%{head}%",)).fetchall()
        finally:
            db.close()
        found = [Path(r[0]) for r in rows if designation_key(Path(r[0]).stem) == key]
        return [p for p in found if p.is_file() and not any(s.lower() in SKIP_DIRS for s in p.parts)]

    return lookup
