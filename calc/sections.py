"""Cross-sections of beams: the rolled ones from the GOST tables, the rest computed from their sizes.

Tables: channels ГОСТ 8240-97 (series У, the same as the old ГОСТ 8240-89)
and I-beams ГОСТ 8239-89 — h, b, s, t, A, mass, Ix, Wx, Sx, Iy, Wy as the
standard gives them.  Computed: rectangular and square tubes ГОСТ 30245-2003
(outer corner radius 2t), round tubes (ГОСТ 10704/8732), bent channels
ГОСТ 8278-83 (inner bend radius t), solid strips and rounds.  A tube or a
bent channel is a polygon with its arcs split into short chords: area, inertia
and the static moment of the half above the neutral axis come out exact to a
fraction of a percent.

For torsion and stability each section also knows: J (St Venant torsion
constant; Bredt's for closed tubes), Wt (τ = T/Wt), Iw (warping constant, open
sections), the flange thickness and the buckling curve of СП 16 (табл. 7:
a — tubes, b — I-beams, strips, rounds, c — channels).

A profile is named as it is said: «швеллер 16», «16У», «двутавр 20»,
«труба 80х40х3», «труба 57х3,5», «гнутый швеллер 100х50х4», «полоса 100х10»
(100 — the height in the plane of bending), «круг 40».
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from .core import CalcError, fmt

# ГОСТ 8240-97, series У: number: h, b, s, t, A cm², mass kg/m, Ix cm⁴, Wx cm³, Sx cm³, Iy cm⁴, Wy cm³
CHANNELS = {
    "5": (50, 32, 4.4, 7.0, 6.16, 4.84, 22.8, 9.1, 5.59, 5.61, 2.75),
    "6.5": (65, 36, 4.4, 7.2, 7.51, 5.90, 48.6, 15.0, 9.0, 8.7, 3.68),
    "8": (80, 40, 4.5, 7.4, 8.98, 7.05, 89.4, 22.4, 13.3, 12.8, 4.75),
    "10": (100, 46, 4.5, 7.6, 10.9, 8.59, 174, 34.8, 20.4, 20.4, 6.46),
    "12": (120, 52, 4.8, 7.8, 13.3, 10.4, 304, 50.6, 29.6, 31.2, 8.52),
    "14": (140, 58, 4.9, 8.1, 15.6, 12.3, 491, 70.2, 40.8, 45.4, 11.0),
    "16": (160, 64, 5.0, 8.4, 18.1, 14.2, 747, 93.4, 54.1, 63.3, 13.8),
    "18": (180, 70, 5.1, 8.7, 20.7, 16.3, 1090, 121, 69.8, 86.0, 17.0),
    "20": (200, 76, 5.2, 9.0, 23.4, 18.4, 1520, 152, 87.8, 113, 20.5),
    "22": (220, 82, 5.4, 9.5, 26.7, 21.0, 2110, 192, 110, 151, 25.1),
    "24": (240, 90, 5.6, 10.0, 30.6, 24.0, 2900, 242, 139, 208, 31.6),
    "27": (270, 95, 6.0, 10.5, 35.2, 27.7, 4160, 308, 178, 262, 37.3),
    "30": (300, 100, 6.5, 11.0, 40.5, 31.8, 5810, 387, 224, 327, 43.6),
}
# ГОСТ 8239-89: number: h, b, s, t, A cm², mass kg/m, Ix cm⁴, Wx cm³, Sx cm³, Iy cm⁴, Wy cm³
I_BEAMS = {
    "10": (100, 55, 4.5, 7.2, 12.0, 9.46, 198, 39.7, 23.0, 17.9, 6.49),
    "12": (120, 64, 4.8, 7.3, 14.7, 11.5, 350, 58.4, 33.7, 27.9, 8.72),
    "14": (140, 73, 4.9, 7.5, 17.4, 13.7, 572, 81.7, 46.8, 41.9, 11.5),
    "16": (160, 81, 5.0, 7.8, 20.2, 15.9, 873, 109, 62.3, 58.6, 14.5),
    "18": (180, 90, 5.1, 8.1, 23.4, 18.4, 1290, 143, 81.4, 82.6, 18.4),
    "20": (200, 100, 5.2, 8.4, 26.8, 21.0, 1840, 184, 104, 115, 23.1),
    "22": (220, 110, 5.4, 8.7, 30.6, 24.0, 2550, 232, 131, 157, 28.6),
    "24": (240, 115, 5.6, 9.5, 34.8, 27.3, 3460, 289, 163, 198, 34.5),
    "27": (270, 125, 6.0, 9.8, 40.2, 31.5, 5010, 371, 210, 260, 41.5),
    "30": (300, 135, 6.5, 10.2, 46.5, 36.5, 7080, 472, 268, 337, 49.9),
}
# sizes for the next-profile suggestions
SQUARE_TUBES = (20, 25, 30, 40, 50, 60, 70, 80, 100, 120, 140, 150, 160, 180, 200)
RECT_TUBES = ((40, 20), (40, 25), (50, 25), (60, 30), (60, 40), (80, 40), (80, 60), (100, 50), (100, 60),
              (120, 60), (120, 80), (140, 60), (140, 100), (150, 100), (160, 80), (180, 100), (200, 100),
              (200, 120), (250, 150), (300, 200))
TUBE_WALLS = (1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 7, 8, 10)
ROUND_TUBES = (21.3, 26.8, 33.5, 42.3, 48, 57, 60, 76, 89, 102, 108, 114, 127, 133, 159, 168, 219, 273)
BENT_CHANNELS = ((60, 32), (80, 40), (80, 50), (100, 50), (120, 50), (120, 60), (140, 60), (160, 60), (160, 80),
                 (180, 70), (200, 80), (200, 100), (250, 125), (300, 100))
BENT_WALLS = (2, 2.5, 3, 4, 5, 6, 8)
ROUNDS = (16, 18, 20, 22, 25, 28, 30, 32, 36, 40, 45, 50, 56, 60, 65, 70, 80, 90, 100, 110, 120)

CHANNEL, I_BEAM, RECT_TUBE, ROUND_TUBE, BENT_CHANNEL, STRIP, ROUND = (
    "channel", "i_beam", "rect_tube", "round_tube", "bent_channel", "strip", "round")
OPEN = (CHANNEL, I_BEAM, BENT_CHANNEL)


@dataclass(frozen=True)
class Section:
    """Properties for bending in the plane of the height (x — the axis across it), mm units."""
    name: str
    family: str
    h: float
    b: float
    area: float        # mm²
    ix: float          # mm⁴
    wx: float          # mm³, the least
    sx: float          # mm³, the static moment of half the section about the neutral axis
    web: float         # mm, the width cut by the neutral axis (for τ = Q·S/(I·b))
    iy: float
    wy: float
    sy: float
    web_y: float
    mass: float = 0.0  # kg/m of steel by the table; 0 — from the area and the density
    sizes: tuple = ()
    j: float = 0.0     # mm⁴, torsion constant
    wt: float = 0.0    # mm³, τ = T/Wt
    iw: float = 0.0    # mm⁶, warping constant (0 — closed or solid: no lateral-torsional buckling)
    tf: float = 0.0    # mm, the flange (or the wall) under a concentrated load
    curve: str = "b"   # buckling curve, СП 16 табл. 7

    @property
    def open(self) -> bool:
        return self.family in OPEN and self.iw > 0

    @property
    def i_min(self) -> float:
        """The least radius of gyration, mm."""
        return math.sqrt(min(self.ix, self.iy) / self.area)

    def mass_per_m(self, density: float = 7.85) -> float:
        return self.mass if self.mass and abs(density - 7.85) < 0.05 else self.area * density / 1000

    def turned(self) -> "Section":
        """The same section bent about its other axis (a tube lying flat, a channel on its back)."""
        return replace(self, name=self.name + " (плашмя)", h=self.b, b=self.h, ix=self.iy, wx=self.wy, sx=self.sy,
                       web=self.web_y, iy=self.ix, wy=self.wx, sy=self.sx, web_y=self.web, iw=0.0)

    def describe(self) -> str:
        return (f"{self.name}: A {fmt(self.area / 100)} см², Ix {fmt(self.ix / 1e4)} см⁴, Wx {fmt(self.wx / 1e3)} см³, "
                f"{fmt(self.mass_per_m())} кг/м")


def _rolled(name: str, family: str, row: tuple) -> Section:
    h, b, s, t, a, mass, ix, wx, sx, iy, wy = row
    if family == I_BEAM:
        sy, web_y = 2 * t * (b / 2) ** 2 / 2, 2 * t   # two flanges, half of each beyond the axis
    else:
        sy, web_y = 2 * t * b ** 2 / 8, 2 * t         # approximate: the flanges as rectangles
    # St Venant torsion of thin rectangles with the factor of rolled profiles (1,2 I-beam, 1,12 channel)
    eta = 1.2 if family == I_BEAM else 1.12
    j = eta / 3 * (2 * b * t ** 3 + (h - 2 * t) * s ** 3)
    if family == I_BEAM:
        iw = iy * 1e4 * (h - t) ** 2 / 4
    else:
        iw = _channel_warping(h - t, b - s / 2, t, s)
    return Section(name, family, h, b, a * 100, ix * 1e4, wx * 1e3, sx * 1e3, s, iy * 1e4, wy * 1e3, sy, web_y,
                   mass, (h, b, s, t), j, j / t, iw, t, "b" if family == I_BEAM else "c")


def _channel_warping(hm: float, bm: float, tf: float, tw: float) -> float:
    """Iw of a channel by its centre lines: tf·b³·h²/12·(3·b·tf + 2·h·tw)/(6·b·tf + h·tw)."""
    return tf * bm ** 3 * hm ** 2 / 12 * (3 * bm * tf + 2 * hm * tw) / (6 * bm * tf + hm * tw)


# ── polygons ────────────────────────────────────────────────────────────────

def _arc(cx: float, cy: float, r: float, a0: float, a1: float, steps: int = 24) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps))) for i in range(steps + 1)]


def _rounded_rect(w: float, h: float, r: float) -> list[tuple[float, float]]:
    """Counter-clockwise, centred at the origin."""
    if r <= 0:
        return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    x, y = w / 2 - r, h / 2 - r
    return (_arc(x, -y, r, -90, 0) + _arc(x, y, r, 0, 90) + _arc(-x, y, r, 90, 180) + _arc(-x, -y, r, 180, 270))


def _channel_outline(h: float, b: float, t: float, r: float) -> list[tuple[float, float]]:
    """A bent channel: the web on the left at x = 0, the flanges to the right, inner bend radius *r*."""
    big = r + t
    if b <= big or h <= 2 * big:
        raise CalcError(f"гнутый швеллер {fmt(h)}х{fmt(b)}х{fmt(t)}: полки короче радиуса гиба")
    return ([(b, 0.0)] + _arc(big, big, big, 270, 180) + _arc(big, h - big, big, 180, 90) + [(b, h), (b, h - t)]
            + _arc(big, h - big, r, 90, 180) + _arc(big, big, r, 180, 270) + [(b, t)])


def _props(poly: list[tuple[float, float]]) -> tuple[float, float, float, float, float]:
    """Area, centroid x, y and second moments about the origin axes Ixx = ∫y², Iyy = ∫x² (signed by the turn)."""
    a = cx = cy = ixx = iyy = 0.0
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
        ixx += (y0 * y0 + y0 * y1 + y1 * y1) * cross
        iyy += (x0 * x0 + x0 * x1 + x1 * x1) * cross
    a /= 2
    sign = 1 if a > 0 else -1   # either way round
    return abs(a), cx / (6 * a), cy / (6 * a), sign * ixx / 12, sign * iyy / 12


def _clip_above(poly: list[tuple[float, float]], level: float, axis: int) -> list[tuple[float, float]]:
    """The part of *poly* where coordinate *axis* ≥ *level* (Sutherland–Hodgman against one line)."""
    out = []
    for p, q in zip(poly, poly[1:] + poly[:1]):
        pin, qin = p[axis] >= level, q[axis] >= level
        if pin:
            out.append(p)
        if pin != qin:
            k = (level - p[axis]) / (q[axis] - p[axis])
            out.append((p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k))
    return out


def _from_polygons(name: str, family: str, outer: list, holes: list, web: float, web_y: float, sizes: tuple) -> Section:
    parts = [(outer, 1)] + [(hole, -1) for hole in holes]
    area = sum(sign * _props(p)[0] for p, sign in parts)
    cx = sum(sign * _props(p)[0] * _props(p)[1] for p, sign in parts) / area
    cy = sum(sign * _props(p)[0] * _props(p)[2] for p, sign in parts) / area
    shifted = [([(x - cx, y - cy) for x, y in p], sign) for p, sign in parts]
    ix = sum(sign * _props(p)[3] for p, sign in shifted)
    iy = sum(sign * _props(p)[4] for p, sign in shifted)

    def static(axis: int) -> float:
        total = 0.0
        for p, sign in shifted:
            clipped = _clip_above(p, 0.0, axis)
            if len(clipped) >= 3:
                a, x, y, _, _ = _props(clipped)
                total += sign * a * (y if axis == 1 else x)
        return abs(total)

    xs = [x for x, _ in shifted[0][0]]
    ys = [y for _, y in shifted[0][0]]
    wx = ix / max(abs(min(ys)), max(ys))
    wy = iy / max(abs(min(xs)), max(xs))
    h, b = max(ys) - min(ys), max(xs) - min(xs)
    return Section(name, family, h, b, area, ix, wx, static(1), web, iy, wy, static(0), web_y, 0.0, sizes)


def rect_tube(h: float, b: float, t: float) -> Section:
    if not (t > 0 and 2 * t < min(h, b)):
        raise CalcError(f"труба {fmt(h)}х{fmt(b)}х{fmt(t)}: стенка не помещается")
    r = 2 * t   # ГОСТ 30245: outer radius about 2t
    name = f"труба {fmt(h)}х{fmt(b)}х{fmt(t)}" if h != b else f"труба {fmt(h)}х{fmt(b)}х{fmt(t)}"
    section = _from_polygons(name, RECT_TUBE, _rounded_rect(b, h, r), [_rounded_rect(b - 2 * t, h - 2 * t, r - t)],
                             2 * t, 2 * t, (h, b, t))
    # Bredt: the area and the length of the centre line of the wall
    rm = r - t / 2
    am = (h - t) * (b - t) - (4 - math.pi) * rm ** 2
    perimeter = 2 * (h - t + b - t) - (8 - 2 * math.pi) * rm
    return replace(section, j=4 * am ** 2 * t / perimeter, wt=2 * am * t, tf=t, curve="a")


def round_tube(d: float, t: float) -> Section:
    if not (t > 0 and 2 * t < d):
        raise CalcError(f"труба {fmt(d)}х{fmt(t)}: стенка не помещается")
    d0 = d - 2 * t
    area = math.pi * (d ** 2 - d0 ** 2) / 4
    i = math.pi * (d ** 4 - d0 ** 4) / 64
    s = (d ** 3 - d0 ** 3) / 12
    return Section(f"труба {fmt(d)}х{fmt(t)}", ROUND_TUBE, d, d, area, i, i / (d / 2), s, 2 * t, i, i / (d / 2), s,
                   2 * t, 0.0, (d, t), 2 * i, 2 * i / (d / 2), 0.0, t, "a")


def bent_channel(h: float, b: float, t: float) -> Section:
    if not (t > 0 and 2 * t < h and 2 * t < b):
        raise CalcError(f"гнутый швеллер {fmt(h)}х{fmt(b)}х{fmt(t)}: стенка не помещается")
    section = _from_polygons(f"гнутый швеллер {fmt(h)}х{fmt(b)}х{fmt(t)}", BENT_CHANNEL, _channel_outline(h, b, t, t),
                             [], t, 2 * t, (h, b, t))
    j = t ** 3 / 3 * (h - t + 2 * (b - t / 2))
    return replace(section, j=j, wt=j / t, iw=_channel_warping(h - t, b - t / 2, t, t), tf=t, curve="c")


def strip(h: float, b: float) -> Section:
    area = b * h
    long, short = max(h, b), min(h, b)
    j = long * short ** 3 / 3 * (1 - 0.63 * short / long)
    return Section(f"полоса {fmt(h)}х{fmt(b)}", STRIP, h, b, area, b * h ** 3 / 12, b * h ** 2 / 6, b * h ** 2 / 8, b,
                   h * b ** 3 / 12, h * b ** 2 / 6, h * b ** 2 / 8, h, 0.0, (h, b), j, j / short, 0.0, b, "b")


def solid_round(d: float) -> Section:
    i = math.pi * d ** 4 / 64
    s = d ** 3 / 12
    return Section(f"круг {fmt(d)}", ROUND, d, d, math.pi * d ** 2 / 4, i, i / (d / 2), s, d, i, i / (d / 2), s, d,
                   0.0, (d,), 2 * i, math.pi * d ** 3 / 16, 0.0, d, "b")


# ── names ───────────────────────────────────────────────────────────────────

_NUM = r"\d+(?:[.,]\d+)?"
_SIZES = re.compile(rf"({_NUM})(?:\s*[хx×*]\s*({_NUM}))?(?:\s*[хx×*]\s*({_NUM}))?")


def _nums(text: str) -> list[float]:
    match = _SIZES.search(text)
    if not match:
        return []
    return [float(g.replace(",", ".")) for g in match.groups() if g]


def _table_key(text: str, table: dict) -> str:
    match = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not match:
        raise CalcError("нет номера профиля")
    key = match.group(1).replace(",", ".")
    key = key[:-2] if key.endswith(".0") else key
    if key not in table:
        raise CalcError(f"номера {key} нет в таблице; есть: {', '.join(table)}")
    return key


def parse(text: str) -> Section:
    """A section by its name: «швеллер 16», «двутавр 20», «труба 80х40х3», «труба 57х3,5»,
    «гнутый швеллер 100х50х4», «полоса 100х10», «круг 40»."""
    raw = (text or "").strip()
    low = raw.lower().replace("ё", "е")
    turned = bool(re.search(r"плашмя|на бок|лежа", low))
    nums = _nums(low)
    section: Section
    if re.search(r"гнут|гн\.?\s*швел|^\[?\s*гн", low):
        if len(nums) != 3:
            raise CalcError("гнутый швеллер — три размера: «гнутый швеллер 100х50х4» (h×b×t)")
        section = bent_channel(*nums)
    elif re.search(r"швел|^\[|^\d+(?:[.,]\d+)?\s*[пу]\b", low):
        key = _table_key(low, CHANNELS)
        section = _rolled(f"швеллер {key.replace('.', ',')}У ГОСТ 8240-97", CHANNEL, CHANNELS[key])
    elif re.search(r"двутавр|^i\s*\d", low):
        key = _table_key(low, I_BEAMS)
        section = _rolled(f"двутавр {key} ГОСТ 8239-89", I_BEAM, I_BEAMS[key])
    elif re.search(r"труб", low) or (len(nums) in (2, 3) and re.search(r"проф|квадрат|прямоуг", low)):
        if len(nums) == 3:
            section = rect_tube(*nums)
        elif len(nums) == 2 and re.search(r"квадрат|проф", low):
            section = rect_tube(nums[0], nums[0], nums[1])
        elif len(nums) == 2:
            section = round_tube(*nums)
        else:
            raise CalcError("труба — «труба 80х40х3» (профильная) или «труба 57х3,5» (круглая)")
    elif re.search(r"полос|лист|прямоугольн", low):
        if len(nums) != 2:
            raise CalcError("полоса — «полоса 100х10»: высота в плоскости изгиба × толщина")
        section = strip(*nums)
    elif re.search(r"квадрат", low) and len(nums) == 1:
        section = strip(nums[0], nums[0])
    elif re.search(r"круг|пруток|ось|вал|^ø|^d\s*\d", low) and nums:
        section = solid_round(nums[0])
    else:
        raise CalcError(f"профиль «{raw}» не понял. Примеры: швеллер 16, двутавр 20, труба 80х40х3, труба 57х3,5, "
                        "гнутый швеллер 100х50х4, полоса 100х10, круг 40")
    return section.turned() if turned else section


def bigger(section: Section) -> list[Section]:
    """The next sections of the same family, lightest first: for «возьмите …»."""
    turned = section.name.endswith("(плашмя)")
    out: list[Section] = []
    if section.family == CHANNEL:
        out = [_rolled(f"швеллер {k.replace('.', ',')}У ГОСТ 8240-97", CHANNEL, row) for k, row in CHANNELS.items()]
    elif section.family == I_BEAM:
        out = [_rolled(f"двутавр {k} ГОСТ 8239-89", I_BEAM, row) for k, row in I_BEAMS.items()]
    elif section.family == RECT_TUBE:
        h, b, _ = section.sizes
        sizes = [(s, s) for s in SQUARE_TUBES] if h == b else list(RECT_TUBES)
        hb = (max(h, b), min(h, b))
        if hb not in sizes:
            sizes.append(hb)
        for H, B in sizes:
            for t in TUBE_WALLS:
                if 2 * t < B and t <= B / 5 + 0.01:
                    out.append(rect_tube(H, B, t) if h >= b else rect_tube(B, H, t))
    elif section.family == ROUND_TUBE:
        for d in ROUND_TUBES:
            for t in TUBE_WALLS:
                if t <= d / 8:
                    out.append(round_tube(d, t))
    elif section.family == BENT_CHANNEL:
        h, b, _ = section.sizes
        sizes = list(BENT_CHANNELS) + ([(h, b)] if (h, b) not in BENT_CHANNELS else [])
        for H, B in sizes:
            for t in BENT_WALLS:
                if t <= min(H, B) / 8:
                    out.append(bent_channel(H, B, t))
    elif section.family == ROUND:
        out = [solid_round(d) for d in ROUNDS]
    elif section.family == STRIP:
        h, b = section.sizes
        out = [strip(h, t) for t in (4, 5, 6, 8, 10, 12, 14, 16, 20, 25, 30)] + \
              [strip(H, b) for H in (40, 50, 60, 80, 100, 120, 150, 200, 250, 300)]
    if section.family in (RECT_TUBE, ROUND_TUBE, BENT_CHANNEL):   # no thinner walls than now: welding, rust
        out = [s for s in out if s.sizes[-1] >= section.sizes[-1] - 1e-9]
    if turned:
        out = [s.turned() for s in out]
    return sorted((s for s in out if s.wx > section.wx * 1.01 or s.ix > section.ix * 1.01), key=lambda s: s.area)
