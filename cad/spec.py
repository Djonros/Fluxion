"""Part templates and the operation list they expand to.

An operation list is plain JSON, independent of KOMPAS:

    {"name": "Пластина", "ops": [
        {"op": "sketch", "z": 0, "shapes": [
            {"rect": {"cx": 0, "cy": 0, "w": 100, "h": 60}},
            {"circle": {"cx": 40, "cy": 20, "d": 8}}]},
        {"op": "extrude", "depth": 10}
    ]}

A sketch lies in a plane parallel to XOY (default, at height ``z``), XOZ or
YOZ (``"plane": "XOZ"``, ``"offset"`` along Y or X).  Shape coordinates are
the global ones of that plane: (x, y) for XOY, (x, z) for XOZ, (y, z) for
YOZ.  ``extrude`` adds material from the last sketch and ``cut`` removes it
(``depth`` or ``"through": true``) along ``"direction"``: "+" or "-" along the
plane's axis (Z, Y, X) or "both"; extrude goes "+" and cut "-" by default.
Closed inner contours of a sketch become holes on extrusion, so a plate with
holes is one sketch and one extrusion.

``{"op": "chamfer", "size": 1, "points": [[x, y, z], ...]}`` chamfers the
edges of the body that pass through the given points (one point per edge).

Templates keep the model's job small: it only picks a template and reads the
sizes out of the text; the geometry below is fixed and tested.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from . import materials, sheet
from .materials import Material


class SpecError(ValueError):
    """The parameters cannot make a valid part."""


@dataclass
class Param:
    name: str
    title: str
    default: float | int | list | None
    kind: str = "float"  # float | int | steps
    minimum: float = 0.0


@dataclass
class Template:
    key: str
    title: str
    hint: str
    params: list[Param]
    build: Callable[[dict[str, Any]], list[dict]]
    example: tuple[str, dict] = field(default=("", {}))
    # main sizes: without them Fluxion asks instead of building with defaults
    required: tuple[str, ...] = ()
    sheet: bool = False  # a bent sheet-metal part: sheet materials, flat pattern and DXF


# ── materials ────────────────────────────────────────────────────────────────
# The catalog lives in cad/materials.json (+ the user's one), see cad/materials.py.

def find_material(text: str, kinds: tuple[str, ...] | None = None) -> Material | None:
    """The material named in free text ("сталь 45", "нержавейка", "рифлёнка 4"), or None."""
    return materials.find(text, kinds=kinds)


# ── shapes ───────────────────────────────────────────────────────────────────

def _rect(w: float, h: float, cx: float = 0.0, cy: float = 0.0) -> dict:
    return {"rect": {"cx": cx, "cy": cy, "w": w, "h": h}}


def _circle(d: float, cx: float = 0.0, cy: float = 0.0) -> dict:
    return {"circle": {"cx": cx, "cy": cy, "d": d}}


def _polygon(n: int, across_flats: float, cx: float = 0.0, cy: float = 0.0) -> dict:
    # circumradius from the size across flats (wrench size)
    r = across_flats / 2 / math.cos(math.pi / n)
    pts = [
        [round(cx + r * math.cos(2 * math.pi * i / n), 4),
         round(cy + r * math.sin(2 * math.pi * i / n), 4)]
        for i in range(n)
    ]
    return {"polyline": {"points": pts}}


def _bolt_circle(count: int, hole_d: float, circle_d: float) -> list[dict]:
    return [
        _circle(
            hole_d,
            round(circle_d / 2 * math.cos(2 * math.pi * i / count), 4),
            round(circle_d / 2 * math.sin(2 * math.pi * i / count), 4),
        )
        for i in range(count)
    ]


def _circle_edges(radii: list[float], heights: list[float]) -> list[list[float]]:
    # a point on each circular edge, at 45° away from the seams of other features
    k = math.cos(math.pi / 4)
    return [[r * k, r * k, z] for z in heights for r in radii if r > 0]


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise SpecError(message)


# ── templates ────────────────────────────────────────────────────────────────

def _plate(p: dict) -> list[dict]:
    L, W, T = p["length"], p["width"], p["thickness"]
    d, off = p["hole_d"], p["hole_offset"]
    shapes = [_rect(L, W)]
    if d > 0:
        _require(off - d / 2 > 0.5, "Отверстия выходят за край: увеличьте отступ или уменьшите диаметр.")
        _require(min(L, W) - 2 * off > d, "Отверстия по углам налезают друг на друга.")
        for sx in (-1, 1):
            for sy in (-1, 1):
                shapes.append(_circle(d, sx * (L / 2 - off), sy * (W / 2 - off)))
    if p["center_hole_d"] > 0:
        _require(p["center_hole_d"] < min(L, W) - 2, "Центральное отверстие больше пластины.")
        shapes.append(_circle(p["center_hole_d"]))
    ops = [{"op": "sketch", "z": 0, "shapes": shapes}, {"op": "extrude", "depth": T}]

    ed = p["end_hole_d"]
    if ed > 0:
        depth = p["end_hole_depth"] or 2 * ed
        _require(ed < T - 1, "Отверстие в торце не помещается в толщину пластины.")
        _require(depth < L / 2 - p["center_hole_d"] / 2 - 1, "Отверстия в торцах слишком глубокие: встречаются в середине.")
        _require(d <= 0 or W / 2 - off - d / 2 > ed / 2 + 0.5 or depth < off - d / 2 - 0.5,
                 "Отверстия в торцах задевают отверстия по углам.")
        # one hole in the middle of each short end face, along X
        for x, direction in ((L / 2, "-"), (-L / 2, "+")):
            ops.append({"op": "sketch", "plane": "YOZ", "offset": x, "shapes": [_circle(ed, 0, T / 2)]})
            ops.append({"op": "cut", "depth": depth, "direction": direction})

    ch = p["chamfer"]
    if ch > 0:
        _require(ch < T / 2, "Фаска больше половины толщины пластины.")
        _require(d <= 0 or ch < off - d / 2 - 0.5, "Фаска задевает отверстия по углам.")
        _require(ed <= 0 or ch < T / 2 - ed / 2 - 0.5, "Фаска задевает отверстия в торцах.")
        points = [[x, y, z] for z in (0, T) for x, y in ((L / 2, 0), (-L / 2, 0), (0, W / 2), (0, -W / 2))]
        ops.append({"op": "chamfer", "size": ch, "points": points})
    return ops


def _flange(p: dict) -> list[dict]:
    D, d, T = p["outer_d"], p["inner_d"], p["thickness"]
    n, hd, bc = p["bolt_count"], p["bolt_d"], p["bolt_circle_d"]
    _require(d < D, "Внутренний диаметр должен быть меньше наружного.")
    shapes = [_circle(D)]
    if d > 0:
        shapes.append(_circle(d))
    if n > 0:
        if bc <= 0:
            bc = (D + d) / 2
        _require(bc + hd < D and bc - hd > d, "Отверстия под болты не помещаются между диаметрами.")
        shapes += _bolt_circle(n, hd, bc)
    return [{"op": "sketch", "z": 0, "shapes": shapes}, {"op": "extrude", "depth": T}]


def _bushing(p: dict) -> list[dict]:
    D, d, L = p["outer_d"], p["inner_d"], p["length"]
    _require(0 <= d < D, "Внутренний диаметр должен быть меньше наружного.")
    shapes = [_circle(D)] + ([_circle(d)] if d > 0 else [])
    ops = [{"op": "sketch", "z": 0, "shapes": shapes}, {"op": "extrude", "depth": L}]
    ch = p["chamfer"]
    if ch > 0:
        # outer and inner edges of both end faces
        _require(2 * ch < (D - d) / 2 and ch < L / 2, "Фаска больше стенки втулки.")
        ops.append({"op": "chamfer", "size": ch, "points": _circle_edges([D / 2, d / 2], [0, L])})
    return ops


def _shaft(p: dict) -> list[dict]:
    steps = p["steps"]
    _require(bool(steps), "У вала должна быть хотя бы одна ступень.")
    ops: list[dict] = []
    z = 0.0
    for i, step in enumerate(steps):
        d, length = float(step["d"]), float(step["length"])
        _require(d > 0 and length > 0, f"Ступень {i + 1}: диаметр и длина должны быть больше нуля.")
        ops.append({"op": "sketch", "z": z, "shapes": [_circle(d)]})
        ops.append({"op": "extrude", "depth": length})
        z += length
    bore = p["bore_d"]
    if bore > 0:
        _require(bore < min(float(s["d"]) for s in steps), "Отверстие шире самой тонкой ступени.")
        ops.append({"op": "sketch", "z": z, "shapes": [_circle(bore)]})
        ops.append({"op": "cut", "through": True})

    pd = p["pin_hole_d"]
    if pd > 0:
        # cross hole along Y, through the whole shaft
        pz = p["pin_hole_z"] or z / 2
        _require(pd / 2 < pz < z - pd / 2, "Поперечное отверстие выходит за торец вала.")
        bottom = 0.0
        for step in steps:
            top = bottom + float(step["length"])
            if bottom < pz + pd / 2 and top > pz - pd / 2:
                _require(pd < float(step["d"]) - 1, "Поперечное отверстие шире ступени вала.")
            bottom = top
        ops.append({"op": "sketch", "plane": "XOZ", "offset": 0, "shapes": [_circle(pd, 0, pz)]})
        ops.append({"op": "cut", "through": True, "direction": "both"})

    ch = p["chamfer"]
    if ch > 0:
        # outer edges of both end faces (and of the bore, if any)
        first, last = steps[0], steps[-1]
        for step in (first, last):
            _require(2 * ch < (float(step["d"]) - bore) / 2 and ch < float(step["length"]) / 2,
                     "Фаска больше торцевой ступени вала.")
        points = _circle_edges([float(first["d"]) / 2, bore / 2], [0]) + _circle_edges([float(last["d"]) / 2, bore / 2], [z])
        ops.append({"op": "chamfer", "size": ch, "points": points})
    return ops


def _bracket(p: dict) -> list[dict]:
    # L profile in XOY (legs along +X and +Y), extruded by width along Z
    a, b, t, w = p["leg_a"], p["leg_b"], p["thickness"], p["width"]
    _require(t < a and t < b, "Толщина больше полки уголка.")
    pts = [[0, 0], [a, 0], [a, t], [t, t], [t, b], [0, b]]
    return [
        {"op": "sketch", "z": 0, "shapes": [{"polyline": {"points": pts}}]},
        {"op": "extrude", "depth": w},
    ]


def _nut(p: dict) -> list[dict]:
    s, d, h = p["wrench_size"], p["hole_d"], p["height"]
    _require(d < s, "Отверстие больше размера под ключ.")
    return [
        {"op": "sketch", "z": 0, "shapes": [_polygon(6, s), _circle(d)]},
        {"op": "extrude", "depth": h},
    ]


TEMPLATES: dict[str, Template] = {t.key: t for t in [
    Template(
        "plate", "Пластина", "прямоугольная пластина, крышка, основание, опционально 4 отверстия по углам и центральное",
        [Param("length", "длина", 100), Param("width", "ширина", 60), Param("thickness", "толщина", 10),
         Param("hole_d", "диаметр отверстий по углам (0 — без них)", 0),
         Param("hole_offset", "отступ центров отверстий от края", 10),
         Param("center_hole_d", "диаметр центрального отверстия (0 — нет)", 0),
         Param("end_hole_d", "диаметр отверстий в двух торцах по длине (0 — нет)", 0),
         Param("end_hole_depth", "глубина отверстий в торцах (0 — два диаметра)", 0),
         Param("chamfer", "фаска по наружному контуру сверху и снизу (0 — нет)", 0)],
        _plate,
        ("пластина 120 на 80 толщиной 10 с четырьмя отверстиями 6 мм в 8 мм от краёв, "
         "в торцах отверстия 5 глубиной 15, фаска 1",
         {"length": 120, "width": 80, "thickness": 10, "hole_d": 6, "hole_offset": 8,
          "end_hole_d": 5, "end_hole_depth": 15, "chamfer": 1}),
        required=("length", "width", "thickness"),
    ),
    Template(
        "flange", "Фланец", "круглый фланец, диск, шайба с отверстиями по окружности",
        [Param("outer_d", "наружный диаметр", 120), Param("inner_d", "внутренний диаметр (0 — сплошной)", 40),
         Param("thickness", "толщина", 12), Param("bolt_count", "число отверстий под болты", 4, "int"),
         Param("bolt_d", "диаметр отверстий под болты", 10),
         Param("bolt_circle_d", "диаметр окружности отверстий (0 — посередине)", 0)],
        _flange,
        ("фланец 150, внутри 50, толщина 15, 6 отверстий М10 на окружности 110",
         {"outer_d": 150, "inner_d": 50, "thickness": 15, "bolt_count": 6, "bolt_d": 11, "bolt_circle_d": 110}),
        required=("outer_d", "thickness"),
    ),
    Template(
        "bushing", "Втулка", "втулка, труба, кольцо, проставка, шайба без отверстий по окружности",
        [Param("outer_d", "наружный диаметр", 30), Param("inner_d", "внутренний диаметр", 20),
         Param("length", "длина", 40),
         Param("chamfer", "фаска на торцах снаружи и внутри (0 — нет)", 0)],
        _bushing,
        ("втулка 30 на 20 длиной 50, фаска 1", {"outer_d": 30, "inner_d": 20, "length": 50, "chamfer": 1}),
        required=("outer_d", "inner_d", "length"),
    ),
    Template(
        "shaft", "Вал", "ступенчатый вал, ось, палец, цилиндр; ступени по порядку от торца",
        [Param("steps", "ступени [{d, length}]", [{"d": 30, "length": 50}, {"d": 20, "length": 40}], "steps"),
         Param("bore_d", "сквозное осевое отверстие (0 — нет)", 0),
         Param("pin_hole_d", "поперечное сквозное отверстие под штифт (0 — нет)", 0),
         Param("pin_hole_z", "высота оси поперечного отверстия от нижнего торца (0 — посередине)", 0),
         Param("chamfer", "фаска на обоих торцах (0 — нет)", 0)],
        _shaft,
        ("вал: 40 мм длиной 60, потом 30 длиной 80, потом 25 длиной 40, "
         "поперечное отверстие 6 на высоте 160, фаски 1",
         {"steps": [{"d": 40, "length": 60}, {"d": 30, "length": 80}, {"d": 25, "length": 40}],
          "pin_hole_d": 6, "pin_hole_z": 160, "chamfer": 1}),
        required=("steps",),
    ),
    Template(
        "bracket", "Уголок", "уголок, кронштейн Г-образный",
        [Param("leg_a", "первая полка", 50), Param("leg_b", "вторая полка", 50),
         Param("thickness", "толщина", 5), Param("width", "ширина", 40)],
        _bracket,
        ("уголок 60x40 толщиной 4, ширина 30", {"leg_a": 60, "leg_b": 40, "thickness": 4, "width": 30}),
        required=("leg_a", "thickness", "width"),
    ),
    Template(
        "nut", "Гайка", "шестигранная гайка или шестигранник",
        [Param("wrench_size", "размер под ключ", 17), Param("hole_d", "диаметр отверстия", 10),
         Param("height", "высота", 8)],
        _nut,
        ("гайка под ключ 13, отверстие 8, высота 6.5", {"wrench_size": 13, "hole_d": 8, "height": 6.5}),
        required=("wrench_size", "hole_d"),
    ),
]}


# ── sheet-metal parts ────────────────────────────────────────────────────────

def _sheet_basics(p: dict) -> tuple[float, float, float]:
    """(thickness, inner radius, K) with the defaults: r = t, K by the table for r/t."""
    t = p["thickness"]
    _require(0.2 <= t <= 30, "Толщина листа — от 0,2 до 30 мм.")
    r = p["radius"] or t
    _require(r >= 0.1 * t, "Радиус сгиба слишком мал для этой толщины (нужно хотя бы 0,1 толщины).")
    k = p["k"] or sheet.k_factor(r, t)
    _require(0 < k <= 0.5, "Коэффициент K — от 0 до 0,5.")
    return t, r, k


def _profile(p: dict, lengths: list[float], turns: list[float], start: float) -> sheet.Profile:
    t, r, k = _sheet_basics(p)
    try:
        profile = sheet.build_profile(lengths, turns, t, r, k, inside=bool(p.get("inside")), start_direction=start)
    except sheet.ProfileError as exc:
        raise SpecError(str(exc)) from None
    for i, straight in enumerate(profile.straights):
        _require(straight >= t, f"Полка {i + 1} слишком короткая: на прямой участок после сгиба остаётся "
                                f"{straight:.1f} мм (нужно хотя бы толщину листа). Удлините полку или уменьшите радиус.")
    return profile


def _sheet_op(profile: sheet.Profile, width: float) -> dict:
    _require(width > 0, "Ширина детали должна быть больше нуля.")
    # the fixed face of the flat pattern: the longest horizontal flange, so the
    # pattern lies in XOY and the drawing view sees it face on; the point is
    # just past the bend, where holes (kept t away from it) never are
    flanges = range(len(profile.straights))
    horizontal = [i for i in flanges if abs(profile.directions[i][1]) < 1e-9] or list(flanges)
    longest = max(horizontal, key=lambda i: profile.straights[i])
    start, end = profile.flange_span(longest)
    x, z = profile.point_on(longest, start + min(profile.thickness / 2, (end - start) / 2))
    return {"op": "sheet", "plane": "XOZ", "profile": [[x_, z_] for x_, z_ in profile.points],
            "thickness": profile.thickness, "radius": profile.radius, "k": profile.k, "width": width,
            "fixed": [round(x, 6), width / 2, round(z, 6)], "bends": len(profile.bends),
            "flat_length": round(profile.flat_length, 4)}


def _flange_holes(profile: sheet.Profile, index: int, d: float, count: int, width: float, what: str) -> list[dict]:
    """*count* holes ⌀*d* in a row across the width, in the middle of the flat part of flange *index*."""
    if d <= 0 or count <= 0:
        return []
    dx, dz = profile.directions[index]
    _require(abs(dx) < 1e-9 or abs(dz) < 1e-9, f"Отверстия в {what}: полка не перпендикулярна осям — "
                                               "отверстия делаются только при сгибах на 90°.")
    start, end = profile.flange_span(index)
    t = profile.thickness
    _require(end - start >= d + 2 * t, f"Отверстия ⌀{d:g} не помещаются в {what}: прямой участок "
                                       f"{end - start:.1f} мм, нужно хотя бы {d + 2 * t:g}.")
    pitch = width / count
    _require(pitch >= d + t, f"{count} отверстий ⌀{d:g} не помещаются по ширине {width:g} мм.")
    x, z = profile.point_on(index, (start + end) / 2)
    ys = [round(pitch * (i + 0.5), 4) for i in range(count)]
    # the sketch lies on the flange face, the cut goes through all into the
    # material (to the right of the profile line) and on, away from the other flanges
    if abs(dz) < 1e-9:  # horizontal flange: holes along Z, sketch in XOY
        return [{"op": "sheet_cut", "plane": "XOY", "offset": round(z, 6), "direction": "+" if -dx > 0 else "-",
                 "shapes": [_circle(d, round(x, 4), y) for y in ys]}]
    return [{"op": "sheet_cut", "plane": "YOZ", "offset": round(x, 6), "direction": "+" if dz > 0 else "-",
             "shapes": [_circle(d, y, round(z, 4)) for y in ys]}]


def _sheet_angle(p: dict) -> list[dict]:
    a, b = p["leg_a"], p["leg_b"] or p["leg_a"]
    angle = p["angle"]
    _require(30 <= angle < 180, "Угол между полками — от 30 до 180°.")
    phi = 180 - angle
    profile = _profile(p, [a, b], [phi], -phi)
    w = p["width"]
    ops = [_sheet_op(profile, w)]
    ops += _flange_holes(profile, 0, p["hole_d"], p["holes_a"], w, "первой полке")
    ops += _flange_holes(profile, 1, p["hole_d"], p["holes_b"], w, "второй полке")
    return ops


def _sheet_channel(p: dict) -> list[dict]:
    h1, c, h2 = p["flange"], p["back"], p["flange2"] or p["flange"]
    profile = _profile(p, [h1, c, h2], [90, 90], -90)
    w = p["width"]
    ops = [_sheet_op(profile, w)]
    ops += _flange_holes(profile, 1, p["hole_d"], p["holes_back"], w, "спинке")
    ops += _flange_holes(profile, 0, p["hole_d"], p["holes_flanges"], w, "первой полке")
    ops += _flange_holes(profile, 2, p["hole_d"], p["holes_flanges"], w, "второй полке")
    return ops


def _sheet_z(p: dict) -> list[dict]:
    f1, h, f2 = p["flange"], p["web"], p["flange2"] or p["flange"]
    profile = _profile(p, [f1, h, f2], [-90, 90], 0)
    w = p["width"]
    return [_sheet_op(profile, w)] + _flange_holes(profile, 1, p["hole_d"], p["holes_web"], w, "стенке")


def _sheet_tray(p: dict) -> list[dict]:
    _require(p["sides"] in (2, 4), "Бортов может быть 2 или 4.")
    length, w_b, h = p["length"], p["width_b"], p["height"]
    if p["sides"] == 4:
        return _sheet_box(p)
    profile = _profile(p, [h, w_b, h], [90, 90], -90)
    return [_sheet_op(profile, length)] + _flange_holes(profile, 1, p["hole_d"], p["holes_bottom"], length, "дне")


def _sheet_box(p: dict) -> list[dict]:
    """Bottom plate with four flanges bent up (KOMPAS "сгиб" on each edge)."""
    t, r, k = _sheet_basics(p)
    length, w_b, h = p["length"], p["width_b"], p["height"]
    inside = bool(p.get("inside"))
    # outer sizes of the bottom plate, flange height from the outer bottom face
    bottom_x, bottom_y = (w_b + 2 * t, length + 2 * t) if inside else (w_b, length)
    height = h + t if inside else h
    _require(min(bottom_x, bottom_y) > 2 * (r + t) + t, "Дно слишком маленькое для такого радиуса и толщины.")
    _require(height - (r + t) >= t, "Борт слишком низкий для такого радиуса: увеличьте высоту или уменьшите радиус.")
    # KOMPAS adds each wall outside the bottom plate, so the plate is the outer
    # size minus the bend zones and the walls stop short of each other: the
    # corners stay open by r + t and the walls never meet
    gap = r + t
    straight_x = bottom_x - 2 * gap
    straight_y = bottom_y - 2 * gap
    arc = math.pi / 2 * (r + k * t)
    flange = height - gap
    ops = [{"op": "sheet_box", "length": bottom_y, "width": bottom_x, "height": height, "thickness": t,
            "radius": r, "k": k, "fixed": [min(t / 2, straight_x / 4), straight_y / 2, 0], "bends": 4, "corner_gap": gap,
            "flat_length": round(straight_x + 2 * (arc + flange), 4),
            "flat_width": round(straight_y + 2 * (arc + flange), 4)}]
    d, n = p["hole_d"], p["holes_bottom"]
    if d > 0 and n > 0:
        _require(straight_x >= d + 2 * t and straight_y / n >= d + t, f"Отверстия ⌀{d:g} не помещаются в дно.")
        ys = [round(straight_y / n * (i + 0.5), 4) for i in range(n)]
        ops.append({"op": "sheet_cut", "plane": "XOY", "offset": 0, "direction": "+",
                    "shapes": [_circle(d, round(straight_x / 2, 4), y) for y in ys]})
    return ops


def _sheet_shelf(p: dict) -> list[dict]:
    _require(p["flanges"] in (1, 2), "Отбортовка может быть с одной или с двух сторон.")
    depth, h = p["depth"], p["flange"]
    if p["flanges"] == 1:
        profile, shelf = _profile(p, [depth, h], [-90], 0), 0
    else:
        profile, shelf = _profile(p, [h, depth, h], [-90, -90], 90), 1
    w = p["length"]
    return [_sheet_op(profile, w)] + _flange_holes(profile, shelf, p["hole_d"], p["holes"], w, "полке")


def _sheet_params(*extra: Param, width_title: str = "ширина детали (вдоль сгиба)", width: float = 100) -> list[Param]:
    return [*extra,
            Param("thickness", "толщина листа", 2),
            Param("width", width_title, width),
            Param("radius", "внутренний радиус сгиба (0 — равен толщине)", 0),
            Param("k", "коэффициент нейтрального слоя K (0 — по таблице для r/t)", 0),
            Param("inside", "размеры полок (по внутренней стороне — 1, по наружной — 0)", 0, "int")]


def _holes(*counts: tuple[str, str]) -> list[Param]:
    return [Param("hole_d", "диаметр отверстий (0 — нет)", 0)] + [Param(n, t, 0, "int") for n, t in counts]


_ANGLE_PARAMS = [Param("leg_a", "первая полка", 40), Param("leg_b", "вторая полка (0 — как первая)", 0),
                 *_sheet_params(width_title="ширина (длина уголка вдоль сгиба)"),
                 Param("angle", "угол между полками, градусов", 90)]

SHEET_TEMPLATES = [
    Template(
        "sheet_angle", "Уголок гнутый", "гнутый уголок из листа, L-профиль, Г-образная деталь из листа",
        _ANGLE_PARAMS + _holes(("holes_a", "число отверстий в первой полке"), ("holes_b", "число отверстий во второй полке")),
        _sheet_angle,
        ("уголок 50х30 из листа 2 мм, длина 100", {"leg_a": 50, "leg_b": 30, "thickness": 2, "width": 100}),
        required=("leg_a", "thickness", "width"), sheet=True,
    ),
    Template(
        "sheet_bracket", "Кронштейн-уголок", "кронштейн из листа: уголок с отверстиями в полках под крепёж",
        _ANGLE_PARAMS + [Param("hole_d", "диаметр отверстий", 9),
                         Param("holes_a", "число отверстий в первой полке", 2, "int"),
                         Param("holes_b", "число отверстий во второй полке", 2, "int")],
        _sheet_angle,
        ("кронштейн 60 на 40 из листа 3, ширина 50, по 2 отверстия 9 мм в каждой полке",
         {"leg_a": 60, "leg_b": 40, "thickness": 3, "width": 50, "hole_d": 9, "holes_a": 2, "holes_b": 2}),
        required=("leg_a", "thickness", "width"), sheet=True,
    ),
    Template(
        "sheet_channel", "П-профиль", "П-образный профиль, швеллер гнутый, П-образный кронштейн из листа",
        [Param("back", "спинка (дно)", 80), Param("flange", "полки", 40),
         Param("flange2", "вторая полка (0 — как первая)", 0), *_sheet_params()]
        + _holes(("holes_back", "число отверстий в спинке"), ("holes_flanges", "число отверстий в каждой полке")),
        _sheet_channel,
        ("П-образный кронштейн, полки по 40, спинка 100, ширина 60, лист 1.5, сгибы R2",
         {"back": 100, "flange": 40, "width": 60, "thickness": 1.5, "radius": 2}),
        required=("back", "flange", "thickness", "width"), sheet=True,
    ),
    Template(
        "sheet_z", "Z-профиль", "Z-образный профиль из листа, ступенька, зет",
        [Param("flange", "верхняя полка", 30), Param("web", "стенка (высота профиля)", 60),
         Param("flange2", "нижняя полка (0 — как верхняя)", 0), *_sheet_params()]
        + _holes(("holes_web", "число отверстий в стенке")),
        _sheet_z,
        ("Z-профиль: полки 30, высота 60, лист 2, длина 200",
         {"flange": 30, "web": 60, "thickness": 2, "width": 200}),
        required=("flange", "web", "thickness", "width"), sheet=True,
    ),
    Template(
        "sheet_tray", "Лоток", "лоток, короб, поддон, коробка из листа с бортами (2 борта — лоток, 4 — короб)",
        [Param("length", "длина (по дну)", 200), Param("width_b", "ширина по дну", 150),
         Param("height", "высота бортов", 30), Param("sides", "число бортов (2 или 4)", 2, "int"),
         Param("thickness", "толщина листа", 1), Param("radius", "внутренний радиус сгиба (0 — равен толщине)", 0),
         Param("k", "коэффициент нейтрального слоя K (0 — по таблице для r/t)", 0),
         Param("inside", "размеры (по внутренней стороне — 1, по наружной — 0)", 0, "int")]
        + _holes(("holes_bottom", "число отверстий в дне")),
        _sheet_tray,
        ("короб 200х150, борт 30, лист 1 мм, оцинковка",
         {"length": 200, "width_b": 150, "height": 30, "sides": 4, "thickness": 1}),
        required=("length", "width_b", "height", "thickness"), sheet=True,
    ),
    Template(
        "sheet_shelf", "Полка с отбортовкой", "полка-пластина из листа с отбортовкой (загнутым краем) с одной или двух сторон",
        [Param("length", "длина полки", 300), Param("depth", "глубина полки", 150),
         Param("flange", "высота отбортовки", 20), Param("flanges", "отбортовка (1 — по одному краю, 2 — по двум)", 1, "int"),
         Param("thickness", "толщина листа", 1.5), Param("radius", "внутренний радиус сгиба (0 — равен толщине)", 0),
         Param("k", "коэффициент нейтрального слоя K (0 — по таблице для r/t)", 0),
         Param("inside", "размеры (по внутренней стороне — 1, по наружной — 0)", 0, "int")]
        + _holes(("holes", "число отверстий в полке")),
        _sheet_shelf,
        ("полка 400 на 200 из листа 1.5 с отбортовкой 25 по двум краям",
         {"length": 400, "depth": 200, "flange": 25, "flanges": 2, "thickness": 1.5}),
        required=("length", "depth", "flange", "thickness"), sheet=True,
    ),
]
TEMPLATES.update({t.key: t for t in SHEET_TEMPLATES})


# ── normalisation ────────────────────────────────────────────────────────────

def _num(value: Any, param: Param) -> float | int:
    try:
        num = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise SpecError(f"Параметр «{param.title}»: не число ({value!r}).") from None
    if not math.isfinite(num) or num < param.minimum:
        raise SpecError(f"Параметр «{param.title}» должен быть не меньше {param.minimum:g}.")
    if num > 10000:
        raise SpecError(f"Параметр «{param.title}»: {num:g} мм — слишком много.")
    return int(round(num)) if param.kind == "int" else num


def _steps(value: Any, param: Param) -> list[dict]:
    if not isinstance(value, list):
        raise SpecError("Ступени вала должны быть списком.")
    out = []
    for item in value[:20]:
        if not isinstance(item, dict):
            raise SpecError("Каждая ступень — это {d, length}.")
        d = item.get("d", item.get("diameter"))
        length = item.get("length", item.get("l"))
        out.append({"d": _num(d, Param("d", "диаметр ступени", 0)),
                    "length": _num(length, Param("length", "длина ступени", 0))})
    return out


def normalize(template: str, params: dict | None) -> tuple[dict, list[str]]:
    """Fill defaults, coerce numbers; return (params, list of assumptions)."""
    tpl = TEMPLATES.get(template)
    if tpl is None:
        raise SpecError(f"Неизвестный тип детали: {template!r}.")
    params = dict(params or {})
    out: dict[str, Any] = {}
    assumed: list[str] = []
    for p in tpl.params:
        raw = params.get(p.name)
        if raw is None or raw == "":
            out[p.name] = p.default
            if p.default not in (0, None):
                assumed.append(f"{p.title}: {p.default}")
            continue
        out[p.name] = _steps(raw, p) if p.kind == "steps" else _num(raw, p)
    return out, assumed


def plain_title(param: Param) -> str:
    """The title without its hint in brackets: "фаска" for "фаска (0 — нет)"."""
    return re.sub(r"\s*[(\[].*?[)\]]", "", param.title)


def missing_params(template: str, params: dict | None) -> list[str]:
    """Titles of the main sizes of *template* that *params* does not give."""
    tpl = TEMPLATES.get(template)
    if tpl is None:
        raise SpecError(f"Неизвестный тип детали: {template!r}.")
    params = params or {}
    by_name = {p.name: p for p in tpl.params}
    return [plain_title(by_name[name]) for name in tpl.required if params.get(name) in (None, "", [])]


def build(template: str, params: dict | None, name: str = "",
          material: Material | None = None) -> tuple[dict, list[str]]:
    """Template + parameters -> operation list ready for the executor."""
    norm, assumed = normalize(template, params)
    tpl = TEMPLATES[template]
    for p in tpl.params:
        if p.kind == "float" and p.default not in (0, None) and norm[p.name] <= 0:
            raise SpecError(f"Параметр «{p.title}» должен быть больше нуля.")
    ops = tpl.build(norm)
    validate_ops(ops)
    part = {"name": name or tpl.title, "template": template, "params": norm, "ops": ops}
    if material is not None:
        part["material"] = material_record(material, norm.get("thickness") if tpl.sheet else None)
    if tpl.sheet:
        body = ops[0]
        part["sheet"] = {"thickness": body["thickness"], "radius": body["radius"], "k": body["k"],
                         "bends": body["bends"], "flat_length": body["flat_length"],
                         "flat_width": body.get("flat_width", body.get("width")),
                         "inside": bool(norm.get("inside"))}
    return part, assumed


def material_record(material: Material, thickness: float | None = None) -> dict:
    """What a part keeps about its material; a checker plate gets the density
    that makes the mass match the table mass of 1 m² for that thickness."""
    record = {"name": material.name, "short": material.short, "density": round(material.density_for(thickness), 4)}
    if material.note:
        record["note"] = material.note
    return record


PLANES = ("XOY", "XOZ", "YOZ")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_ops(ops: list[dict]) -> None:
    """Check an operation list by structure (also for hand-written JSON)."""
    _require(isinstance(ops, list) and ops, "Пустой список операций.")
    have_sketch = have_body = sheet_body = False
    for i, op in enumerate(ops, 1):
        kind = op.get("op") if isinstance(op, dict) else None
        if kind == "sketch":
            shapes = op.get("shapes")
            _require(isinstance(shapes, list) and shapes, f"Операция {i}: эскиз без контуров.")
            for s in shapes:
                _require(isinstance(s, dict) and len(s) == 1 and next(iter(s)) in ("rect", "circle", "polyline"),
                         f"Операция {i}: неизвестный контур {s!r}.")
            _require(op.get("plane", "XOY") in PLANES, f"Операция {i}: неизвестная плоскость {op.get('plane')!r}.")
            _require(_is_number(op.get("offset", op.get("z", 0))), f"Операция {i}: смещение плоскости — не число.")
            have_sketch = True
        elif kind in ("extrude", "cut"):
            _require(have_sketch, f"Операция {i}: {kind} без эскиза.")
            _require(op.get("through") is True or float(op.get("depth", 0)) > 0,
                     f"Операция {i}: нужна глубина больше нуля.")
            _require(op.get("direction", "+") in ("+", "-", "both"),
                     f"Операция {i}: направление — «+», «-» или «both».")
            have_sketch = False
            have_body = have_body or kind == "extrude"
        elif kind in ("sheet", "sheet_box"):
            _require(not have_body, f"Операция {i}: листовое тело должно быть первым.")
            for key in ("thickness", "radius", "k"):
                _require(_is_number(op.get(key)) and op[key] > 0, f"Операция {i}: {key} — число больше нуля.")
            sizes = ("width",) if kind == "sheet" else ("length", "width", "height")
            for key in sizes:
                _require(_is_number(op.get(key)) and op[key] > 0, f"Операция {i}: {key} — число больше нуля.")
            if kind == "sheet":
                profile = op.get("profile")
                _require(isinstance(profile, list) and len(profile) >= 2
                         and all(isinstance(pt, list) and len(pt) == 2 and all(map(_is_number, pt)) for pt in profile),
                         f"Операция {i}: профиль листа — список точек [x, z], хотя бы две.")
            fixed = op.get("fixed")
            _require(fixed is None or (isinstance(fixed, list) and len(fixed) == 3 and all(map(_is_number, fixed))),
                     f"Операция {i}: неподвижная грань развёртки — точка [x, y, z].")
            have_body = True
            sheet_body = True
        elif kind == "sheet_cut":
            _require(have_body and sheet_body, f"Операция {i}: вырез в листе без листового тела.")
            shapes = op.get("shapes")
            _require(isinstance(shapes, list) and shapes and all(isinstance(s, dict) and len(s) == 1
                     and next(iter(s)) in ("rect", "circle", "polyline") for s in shapes),
                     f"Операция {i}: вырез без контуров.")
            _require(op.get("plane", "XOY") in PLANES and _is_number(op.get("offset", 0)),
                     f"Операция {i}: плоскость выреза — XOY, XOZ или YOZ со смещением.")
            _require(op.get("direction", "-") in ("+", "-"), f"Операция {i}: направление выреза — «+» или «-».")
        elif kind == "chamfer":
            _require(have_body, f"Операция {i}: фаска без тела.")
            _require(_is_number(op.get("size")) and float(op["size"]) > 0, f"Операция {i}: размер фаски больше нуля.")
            points = op.get("points")
            _require(isinstance(points, list) and points
                     and all(isinstance(pt, list) and len(pt) == 3 and all(map(_is_number, pt)) for pt in points),
                     f"Операция {i}: фаске нужны точки рёбер [x, y, z].")
        else:
            raise SpecError(f"Операция {i}: неизвестный тип {kind!r}.")
