"""Work in a product: hours of design by the number and complexity of the parts,
and the surface for shot blasting and painting.

Hours: every detail designed once (copies are not work) gets a category by the
operations of its build tree — "простая", "средняя", "сложная" — and the hours
of that category; an assembly gets a base plus a share per component. The norms
are a file the department edits: %APPDATA%\\Fluxion\\time_norms.json (made with
the defaults on first use).

Painting: the surface of the details made here, without the ones that are not
painted (stainless steel, aluminium, galvanised sheet, brass and bronze,
plastics, rubber) and without the faces hidden in the weld joints.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .assembly import ASSEMBLY, material_text

DEFAULT_NORMS = {
    "_": "Нормы времени на модель. Деталь — по числу операций дерева построения (без эскизов и "
         "вспомогательных плоскостей); сборка — база плюс доля на каждый компонент. Часы.",
    "detail": [
        {"category": "простая", "max_operations": 3, "hours": 0.5},
        {"category": "средняя", "max_operations": 10, "hours": 1.5},
        {"category": "сложная", "max_operations": None, "hours": 3.0},
    ],
    "assembly": {"base_hours": 0.25, "hours_per_component": 0.02},
}

# not operations: sketches and the auxiliary geometry around them
_AUXILIARY = re.compile(
    r"^(эскиз|смещенная плоскость|плоскость|ось|точка|локальная система координат|контрольная точка|"
    r"присоединяемая точка|массив точек|группа|зеркальный массив тел)\b", re.IGNORECASE)
_SKETCH = re.compile(r"^эскиз\b", re.IGNORECASE)

# not painted: stainless, aluminium, galvanised, copper alloys, plastics and rubber
NOT_PAINTED_RE = re.compile(
    r"12х18|08х18|aisi\s*3|нерж|amг|амг|д16|ад31|ад0|алюмин|оцинк|цинк|латун|бронз|\bл63|\bбр|"
    r"капролон|фторопласт|полиамид|полиэт|резин|смесь резин|паронит",
    re.IGNORECASE,
)


def norms_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Fluxion" / "time_norms.json"


def load_norms(create: bool = True) -> dict:
    """The norms of the department; the default file is written on first use so it can be edited."""
    path = norms_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("detail") and data.get("assembly"):
            return data
    except (OSError, ValueError):
        pass
    if create:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(DEFAULT_NORMS, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    return DEFAULT_NORMS


def operations(features: list[str]) -> tuple[int, int]:
    """(operations, sketches) of a build tree."""
    ops = sum(1 for f in features if not _AUXILIARY.match(f))
    sketches = sum(1 for f in features if _SKETCH.match(f))
    return ops, sketches


def category(ops: int, norms: dict) -> tuple[str, float]:
    for row in norms["detail"]:
        limit = row.get("max_operations")
        if limit is None or ops <= limit:
            return row["category"], float(row["hours"])
    last = norms["detail"][-1]
    return last["category"], float(last["hours"])


@dataclass
class WorkRow:
    title: str
    kind: str  # "Деталь" | "Сборочная единица"
    operations: int  # of a detail; components of an assembly
    sketches: int
    category: str
    hours: float


def workload(report, norms: dict | None = None) -> list[WorkRow]:
    """Hours of design of a product: its details once each, its assemblies."""
    norms = norms or load_norms()
    rows = []
    for line in sorted(report.details, key=lambda l: (l.component.marking or "яя", l.component.name)):
        c = line.component
        ops, sketches = operations(c.features)
        name, hours = category(ops, norms)
        rows.append(WorkRow(c.title, "Деталь", ops, sketches, name, hours))
    a = norms["assembly"]
    for section in report.sections:
        components = sum(line.qty for line in section.lines)
        hours = float(a["base_hours"]) + float(a["hours_per_component"]) * components
        rows.append(WorkRow(section.assembly.title, "Сборочная единица", components, 0, "сборка", round(hours, 2)))
    return rows


# ── painting ─────────────────────────────────────────────────────────────────


def painted(material_raw: str) -> bool:
    return not NOT_PAINTED_RE.search(material_text(material_raw).lower().replace("ё", "е"))


@dataclass
class Paint:
    details_m2: float  # surface of all the details made here
    not_painted_m2: float  # stainless, aluminium, galvanised...
    joints_m2: float  # faces hidden in the weld joints
    by_material: list[tuple[str, float, bool]]  # (material, m², painted)

    @property
    def paint_m2(self) -> float:
        return max(0.0, self.details_m2 - self.not_painted_m2 - self.joints_m2)


def paint(report) -> Paint:
    totals: dict[str, list] = {}
    for line in report.details:
        c = line.component
        name = material_text(c.material) or "Материал не указан"
        row = totals.setdefault(name, [0.0, painted(c.material)])
        row[0] += c.area_m2 * line.qty_product
    details = sum(v[0] for v in totals.values())
    not_painted = sum(v[0] for v in totals.values() if not v[1])
    # both faces of a joint are hidden: twice the common area, per assembly on the product
    joints = sum(2 * j.area_mm2 * j.count * w.qty_product for w in report.welds for j in w.joints) / 1e6
    rows = sorted(((m, v[0], v[1]) for m, v in totals.items()), key=lambda r: -r[1])
    return Paint(details, not_painted, joints, rows)
