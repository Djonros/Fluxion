"""Чертёж DXF/DWG → текстовая сводка для диалога «/компас».

Модель текст2CAD читает описание словами; здесь чертёж переводится в такой же
текст: габарит, внешний контур, окружности как отверстия (диаметры и центры),
размеры из DIMENSION и надписи. DWG сначала открывается через AutoCAD или
ODA File Converter (cad/acad.py), DXF читает cad/drawing.py. Сводка
добавляется к описанию детали, и модель заполняет параметры шаблона
(пластина, фланец) только тем, что есть в чертеже.
"""
from __future__ import annotations

from pathlib import Path

from .drawing import MODEL

SUFFIXES = {".dxf", ".dwg"}

TEXT_LIMIT = 12  # надписей в сводке не больше
TEXT_LENGTH = 80  # символов в надписи не больше
DIM_LIMIT = 24  # размеров в сводке не больше
HOLE_GROUPS = 6  # групп отверстий по диаметру не больше
CENTERS = 8  # центров на группу не больше

_GEOMETRY = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "SPLINE", "ELLIPSE"}
_DIM_SIZES = {"linear", "ordinate"}


class DrawingInputError(RuntimeError):
    """Чертёж не читается или в нём нет геометрии: сообщение для конструктора."""


def _g(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _extents(boxes) -> tuple[float, float, float, float] | None:
    xs0 = [b[0] for b in boxes]
    ys0 = [b[1] for b in boxes]
    xs1 = [b[2] for b in boxes]
    ys1 = [b[3] for b in boxes]
    if not xs0:
        return None
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _hole_lines(circles) -> list[str]:
    """«⌀20 — 2 шт., центры: (50, 50), (150, 50)» по группам одинакового диаметра."""
    groups: dict[float, list[tuple[float, float]]] = {}
    for e in circles:
        if not e.box:
            continue
        cx, cy = (e.box[0] + e.box[2]) / 2, (e.box[1] + e.box[3]) / 2
        r = (e.box[2] - e.box[0]) / 2
        if r <= 0:
            continue
        groups.setdefault(round(r * 2, 6), []).append((cx, cy))
    out = []
    for d in sorted(groups, reverse=True)[:HOLE_GROUPS]:
        points = groups[d]
        centers = ", ".join(f"({_g(x)}, {_g(y)})" for x, y in points[:CENTERS])
        more = f" … ещё {len(points) - CENTERS}" if len(points) > CENTERS else ""
        out.append(f"⌀{_g(d)} — {len(points)} шт., центры: {centers}{more}")
    return out


def _dim_lines(model) -> list[str]:
    sizes, diameters, radii, angles = [], [], [], []
    for e in model:
        if e.kind != "DIMENSION" or e.value is None:
            continue
        if e.dim_kind == "diameter":
            diameters.append(e.value)
        elif e.dim_kind == "radius":
            radii.append(e.value)
        elif e.dim_kind == "angular":
            angles.append(e.value)
        elif e.dim_kind in _DIM_SIZES:
            sizes.append(e.value)
    out = []
    if sizes:
        shown = sorted({round(v, 6) for v in sizes})[:DIM_LIMIT]
        out.append("Линейные размеры: " + ", ".join(_g(v) for v in shown) + ".")
    if diameters:
        shown = sorted({round(v, 6) for v in diameters})[:DIM_LIMIT]
        out.append("Размеры диаметров: " + ", ".join(f"⌀{_g(v)}" for v in shown) + ".")
    if radii:
        shown = sorted({round(v, 6) for v in radii})[:DIM_LIMIT]
        out.append("Радиусы: " + ", ".join(f"R{_g(v)}" for v in shown) + ".")
    if angles:
        shown = sorted({round(v, 3) for v in angles})[:DIM_LIMIT]
        out.append("Углы: " + ", ".join(f"{_g(v)}°" for v in shown) + ".")
    return out


def _text_line(model) -> str:
    texts: list[str] = []
    for e in model:
        if e.kind not in ("TEXT", "MTEXT", "BLOCKTEXT", "ATTRIB"):
            continue
        clean = " ".join(e.text.split())[:TEXT_LENGTH]
        if clean and clean not in texts:
            texts.append(clean)
        if len(texts) >= TEXT_LIMIT:
            break
    return "Надписи: " + ", ".join(f"«{t}»" for t in texts) + "." if texts else ""


def summarize(path: str | Path, log=print) -> str:
    """Сводка одного чертежа — текстовый блок для модели."""
    from .acad import open_drawing
    from .drawing import DrawingError

    path = Path(path)
    try:
        drawing = open_drawing(path, log=log)
    except DrawingError as exc:
        raise DrawingInputError(str(exc)) from exc
    model = drawing.in_space(MODEL)
    geometry = [e for e in model if e.kind in _GEOMETRY and e.box]
    if not geometry:
        raise DrawingInputError("в пространстве модели нет геометрии.")
    ext = _extents([e.box for e in geometry])
    width, height = ext[2] - ext[0], ext[3] - ext[1]
    lines = [f"[Чертёж {path.name}]", f"Габарит: {_g(width)} × {_g(height)} мм."]
    lines += _contour_lines(geometry, width, height)
    holes = [e for e in geometry if e.kind == "CIRCLE"]
    outer = 0.0
    if holes and not [e for e in geometry if e.kind != "CIRCLE"]:
        outer = max((e.box[2] - e.box[0]) / 2 * 2 for e in holes)  # largest circle: the outline itself
        lines.append(f"Внешний контур: окружность ⌀{_g(outer)} мм.")
        holes = [e for e in holes if round(e.box[2] - e.box[0], 6) != round(outer, 6)]
    if holes:
        lines.append(f"Отверстия (окружности): {len(holes)} шт.")
        lines += _hole_lines(holes)
    lines += _dim_lines(model)
    text = _text_line(model)
    if text:
        lines.append(text)
    return "\n".join(lines)


def _contour_lines(geometry, width: float, height: float) -> list[str]:
    closed = [e for e in geometry if e.kind in ("LWPOLYLINE", "POLYLINE") and e.closed]
    lines = [e for e in geometry if e.kind == "LINE"]
    if closed:
        outer = max(closed, key=lambda e: _area(e.box))
        box = outer.box
        out = [f"Внешний контур: замкнутая полилиния {_g(box[2] - box[0])} × {_g(box[3] - box[1])} мм."]
        inner = len(closed) - 1
        if inner:
            out.append(f"Замкнутых контуров внутри: {inner} (пазы, вырезы).")
        return out
    if lines:
        return [f"Внешний контур: отрезки, габарит {_g(width)} × {_g(height)} мм."]
    return []


def blocks(paths, log=print) -> tuple[str, list[str]]:
    """Сводки нескольких чертежей: текст к описанию и проблемы (не прочитано)."""
    parts: list[str] = []
    problems: list[str] = []
    for path in paths:
        try:  # один битый файл не должен отменять сводки остальных
            parts.append(summarize(path, log=log))
        except Exception as exc:
            problems.append(f"{Path(path).name}: {exc}")
    return "\n\n".join(parts), problems
