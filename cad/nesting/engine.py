"""The nesting: details of the true shape on remnants and sheets.

A detail and a sheet are rasters (a cell of ``res`` mm, 2 mm on a 6000×1500 sheet): a cell
of a detail is taken when its centre is inside the shape or the contour crosses it, and
the taken cells are grown by about half the gap between details. A place of a detail on
a sheet is free when its cells meet no taken cell of the sheet — all such places at once
by an FFT correlation. The detail goes to the free place that ends nearest to the start
of the sheet (then lowest), in the turn of 0/90/180/270° that does it best, so the
details pack from one end and the rest of the sheet stays a whole strip — a new remnant.
The holes of a detail are free cells: small details go into them.

Every place is checked on the true contours before it is taken: the gap to the nearest
detail and the margin to the edge of the sheet, in mm.

Big details go first. Sheets: the remnants that fit the material (smallest first), then
whole sheets of the list, then standard sheets (6000×1500 by default) as many as needed.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from .materials import SheetMaterial
from .parts import Part
from .stock import StockSheet, standard_sheets


@dataclass
class Settings:
    gap: float = 8.0  # mm between details
    margin: float = 10.0  # mm from the edge of a sheet
    resolution: float = 0.0  # mm a cell; 0 — by the sheet (about 2.5 million cells)
    rotations: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)
    min_remnant: tuple[float, float] = (300.0, 300.0)  # a strip smaller than this is scrap
    use_remnants: bool = True
    any_grade: bool = False  # a remnant of another grade of the same thickness will do
    order: str = ""  # the order number: remnants kept for it are free
    sheet_sizes: tuple[tuple[float, float], ...] = ((6000.0, 1500.0),)
    passes: int = 2  # orders of the details tried; the better nesting is kept


@dataclass
class Placement:
    part: Part
    angle: float
    dx: float
    dy: float
    loops: list[np.ndarray]  # the contours on the sheet, mm
    box: tuple[float, float, float, float]

    @property
    def center(self) -> tuple[float, float]:
        """Where to write its number: the centroid when it is on the metal, else the box centre."""
        from .parts import _inside

        outer = self.loops[0]
        x, y = outer[:, 0], outer[:, 1]
        xn, yn = np.roll(x, -1), np.roll(y, -1)
        cross = x * yn - xn * y
        area = cross.sum() / 2
        if abs(area) > 1e-9:
            cx = float(((x + xn) * cross).sum() / (6 * area))
            cy = float(((y + yn) * cross).sum() / (6 * area))
            point = (cx, cy)
            if _inside(point, [tuple(p) for p in outer]) and not any(
                    _inside(point, [tuple(p) for p in hole]) for hole in self.loops[1:]):
                return point
        x0, y0, x1, y1 = self.box
        return (x0 + x1) / 2, (y0 + y1) / 2


@dataclass
class NestedSheet:
    stock: StockSheet
    placements: list[Placement] = field(default_factory=list)
    cut_x: float = 0.0  # where the new remnant is cut off; 0 — no remnant
    remnant: StockSheet | None = None

    @property
    def length(self) -> float:
        return self.stock.length

    @property
    def width(self) -> float:
        return self.stock.width

    @property
    def parts_area(self) -> float:
        return sum(p.part.shape.area for p in self.placements)

    @property
    def used_area(self) -> float:
        """The sheet without the new remnant, mm²."""
        return (self.cut_x if self.remnant else self.length) * self.width

    @property
    def utilization(self) -> float:
        return self.parts_area / self.used_area if self.used_area else 0.0

    @property
    def cut_length(self) -> float:
        return sum(p.part.shape.cut_length for p in self.placements)

    @property
    def pierces(self) -> int:
        return sum(p.part.shape.pierces for p in self.placements)


@dataclass
class GroupResult:
    material: SheetMaterial
    sheets: list[NestedSheet] = field(default_factory=list)
    unplaced: dict[int, int] = field(default_factory=dict)  # id(part) → copies left
    too_big: list[Part] = field(default_factory=list)


@dataclass
class Result:
    groups: list[GroupResult]
    settings: Settings
    seconds: float = 0.0


# ── raster ───────────────────────────────────────────────────────────────────


def _fast_len(n: int) -> int:
    """The least 2^a·3^b·5^c ≥ n: an FFT of that length is quick."""
    best = 1 << (n - 1).bit_length()
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            p = p35
            while p < n:
                p *= 2
            best = min(best, p)
            p35 *= 3
        p5 *= 5
    return best


def free_places(grid: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Bool (H-h+1, W-w+1): where *mask* put with its corner at (i, j) meets no taken cell."""
    H, W = grid.shape
    h, w = mask.shape
    if h > H or w > W:
        return np.zeros((0, 0), dtype=bool)
    shape = (_fast_len(H + h - 1), _fast_len(W + w - 1))
    spectrum = np.fft.rfft2(grid.astype(np.float32), shape) * np.fft.rfft2(mask[::-1, ::-1].astype(np.float32), shape)
    overlap = np.fft.irfft2(spectrum, shape)
    return overlap[h - 1:H, w - 1:W] < 0.5


def rotate(points: np.ndarray, angle: float) -> np.ndarray:
    if not angle:
        return points
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    if abs(c) < 1e-12:
        c = 0.0
    if abs(s) < 1e-12:
        s = 0.0
    return points @ np.array([[c, s], [-s, c]])


@dataclass
class Raster:
    mask: np.ndarray  # bool (h, w)
    key: tuple  # (id of the part, turn, cell, growth): the cache of its spectra
    origin: tuple[float, float]  # mm of the corner of cell (0, 0), the shape turned
    loops: list[np.ndarray]  # the turned contours, mm


def rasterize(loops: list[np.ndarray], res: float, grow: int, key: tuple = ()) -> Raster:
    """The cells of a shape (even-odd of its loops), grown by *grow* cells."""
    x0 = min(float(l[:, 0].min()) for l in loops)
    y0 = min(float(l[:, 1].min()) for l in loops)
    x1 = max(float(l[:, 0].max()) for l in loops)
    y1 = max(float(l[:, 1].max()) for l in loops)
    pad = grow + 1
    ox, oy = x0 - pad * res, y0 - pad * res
    w = math.ceil((x1 - ox) / res) + pad + 1
    h = math.ceil((y1 - oy) / res) + pad + 1
    cells = np.zeros((h, w), dtype=bool)
    edges = np.concatenate([np.stack([l, np.roll(l, -1, axis=0)], axis=1) for l in loops])  # (E, 2, 2)
    ax, ay, bx, by = edges[:, 0, 0], edges[:, 0, 1], edges[:, 1, 0], edges[:, 1, 1]
    for r in range(h):
        y = oy + (r + 0.5) * res
        cross = (ay > y) != (by > y)
        if not cross.any():
            continue
        xs = np.sort(ax[cross] + (y - ay[cross]) * (bx[cross] - ax[cross]) / (by[cross] - ay[cross]))
        for xa, xb in zip(xs[0::2], xs[1::2]):
            c0 = max(0, math.ceil((xa - ox) / res - 0.5))
            c1 = min(w - 1, math.floor((xb - ox) / res - 0.5))
            if c1 >= c0:
                cells[r, c0:c1 + 1] = True
    # the contour itself: thin bits narrower than a cell are taken too
    length = np.hypot(bx - ax, by - ay)
    steps = np.maximum(1, np.ceil(length / (res / 3)).astype(int))
    t = np.concatenate([np.linspace(0, 1, n + 1) for n in steps])
    idx = np.repeat(np.arange(len(edges)), steps + 1)
    px = ax[idx] + (bx[idx] - ax[idx]) * t
    py = ay[idx] + (by[idx] - ay[idx]) * t
    cells[np.clip(((py - oy) / res).astype(int), 0, h - 1), np.clip(((px - ox) / res).astype(int), 0, w - 1)] = True
    if grow > 0:
        grown = cells.copy()
        for di in range(-grow, grow + 1):
            for dj in range(-grow, grow + 1):
                if di * di + dj * dj > grow * grow + grow or (di == 0 and dj == 0):
                    continue
                src = cells[max(0, -di):h - max(0, di), max(0, -dj):w - max(0, dj)]
                grown[max(0, di):h - max(0, -di), max(0, dj):w - max(0, -dj)] |= src
        cells = grown
    return Raster(cells, key, (ox, oy), loops)


# ── exact checks ─────────────────────────────────────────────────────────────


def _points_to_segments(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    if len(points) == 0 or len(a) == 0:
        return math.inf
    best = math.inf
    d = b - a
    dd = (d * d).sum(axis=1)
    dd[dd == 0] = 1e-12
    for start in range(0, len(points), 512):
        p = points[start:start + 512]
        t = ((p[:, None, :] - a[None, :, :]) * d[None, :, :]).sum(axis=2) / dd[None, :]
        np.clip(t, 0, 1, out=t)
        q = a[None, :, :] + t[:, :, None] * d[None, :, :]
        best = min(best, float(np.sqrt(((p[:, None, :] - q) ** 2).sum(axis=2).min())))
    return best


def loops_distance(first: list[np.ndarray], second: list[np.ndarray]) -> float:
    """The least distance between the contours of two details, mm."""
    pa = np.concatenate(first)
    pb = np.concatenate(second)
    sa = (np.concatenate(first), np.concatenate([np.roll(l, -1, axis=0) for l in first]))
    sb = (np.concatenate(second), np.concatenate([np.roll(l, -1, axis=0) for l in second]))
    return min(_points_to_segments(pa, *sb), _points_to_segments(pb, *sa))


def _box(loops: list[np.ndarray]) -> tuple[float, float, float, float]:
    outer = loops[0]
    return (float(outer[:, 0].min()), float(outer[:, 1].min()), float(outer[:, 0].max()), float(outer[:, 1].max()))


# ── one sheet ────────────────────────────────────────────────────────────────


def cell_size(sheet: StockSheet, settings: Settings) -> float:
    if settings.resolution > 0:
        return settings.resolution
    return max(1.0, math.ceil(math.sqrt(sheet.area / 2.5e6) * 2) / 2)


class SheetNester:
    """Details placed one by one on one sheet."""

    def __init__(self, sheet: StockSheet, settings: Settings, cache: dict):
        self.sheet = sheet
        self.settings = settings
        self.res = cell_size(sheet, settings)
        self.grow = math.ceil(settings.gap / 2 / self.res) + 1
        self.cache = cache
        H = math.floor(sheet.width / self.res)
        W = math.floor(sheet.length / self.res)
        self.grid = np.zeros((H, W), dtype=bool)
        border = max(0, math.ceil(settings.margin / self.res) - self.grow)
        if border:
            self.grid[:border, :] = self.grid[-border:, :] = True
            self.grid[:, :border] = self.grid[:, -border:] = True
        self.placed: list[Placement] = []

    def raster(self, part: Part, angle: float) -> Raster:
        key = (id(part), angle, self.res, self.grow)
        if key not in self.cache:
            loops = [rotate(np.asarray(l, dtype=float), angle) for l in part.shape.loops]
            self.cache[key] = rasterize(loops, self.res, self.grow, key)
        return self.cache[key]

    def _fits(self, loops: list[np.ndarray], box) -> bool:
        m, g = self.settings.margin - 1e-6, self.settings.gap - 1e-6
        x0, y0, x1, y1 = box
        if x0 < m or y0 < m or x1 > self.sheet.length - m or y1 > self.sheet.width - m:
            return False
        for other in self.placed:
            ox0, oy0, ox1, oy1 = other.box
            if x0 - g > ox1 or ox0 - g > x1 or y0 - g > oy1 or oy0 - g > y1:
                continue
            if loops_distance(loops, other.loops) < g:
                return False
        return True

    def _free_maps(self, rasters: dict) -> dict:
        """The free places of every turn: one spectrum of the sheet for all of them."""
        H, W = self.grid.shape
        fitting = {a: r for a, r in rasters.items() if r.mask.shape[0] <= H and r.mask.shape[1] <= W}
        maps = {a: np.zeros((0, 0), dtype=bool) for a in rasters}
        if not fitting:
            return maps
        # a circular correlation on the sheet itself: a place inside the sheet never wraps round
        shape = (_fast_len(H), _fast_len(W))
        sheet = np.fft.rfft2(self.grid.astype(np.float32), shape)
        for a, r in fitting.items():
            key = ("spectrum", r.key, shape)
            if key not in self.cache:
                self.cache[key] = np.conj(np.fft.rfft2(r.mask.astype(np.float32), shape))
            h, w = r.mask.shape
            maps[a] = np.fft.irfft2(sheet * self.cache[key], shape)[:H - h + 1, :W - w + 1] < 0.5
        return maps

    def _no_fit(self, placed: Raster, other: Raster) -> np.ndarray:
        """Bool (ha+hb-1, wa+wb-1): the offsets d of *other* from *placed* where they meet;
        index k ↔ d = k − (hb − 1). The same for every two copies: kept in the cache."""
        key = ("nofit", placed.key, other.key)
        if key not in self.cache:
            ha, wa = placed.mask.shape
            hb, wb = other.mask.shape
            shape = (_fast_len(ha + hb - 1), _fast_len(wa + wb - 1))
            full = np.fft.irfft2(np.fft.rfft2(placed.mask.astype(np.float32), shape) *
                                 np.fft.rfft2(other.mask[::-1, ::-1].astype(np.float32), shape), shape)
            self.cache[key] = full[:ha + hb - 1, :wa + wb - 1] > 0.5
        return self.cache[key]

    def place_all(self, part: Part, count: int, angles: tuple[float, ...]) -> int:
        """Up to *count* copies of *part*; how many went on the sheet."""
        rasters = {a: self.raster(part, a) for a in angles}
        free = self._free_maps(rasters)
        done = 0
        while done < count:
            placed = False
            for _ in range(200):  # places refused by the exact check
                best = None
                for a, places in free.items():
                    if places.size == 0:
                        continue
                    cols = np.flatnonzero(places.any(axis=0))
                    if len(cols) == 0:
                        continue
                    w = rasters[a].mask.shape[1]
                    j = int(cols[0])
                    i = int(np.flatnonzero(places[:, j])[0])
                    end = j + w
                    if best is None or (end, i) < best[0]:
                        best = ((end, i), a, i, j)
                if best is None:
                    break
                _, a, i, j = best
                r = rasters[a]
                dx, dy = j * self.res - r.origin[0], i * self.res - r.origin[1]
                loops = [l + np.array([dx, dy]) for l in r.loops]
                box = _box(loops)
                if not self._fits(loops, box):
                    free[a][i, j] = False
                    continue
                self.placed.append(Placement(part, a, dx, dy, loops, box))
                h, w = r.mask.shape
                self.grid[i:i + h, j:j + w] |= r.mask
                for b, places in free.items():  # the places this copy took from the next ones
                    if places.size == 0:
                        continue
                    nofit = self._no_fit(r, rasters[b])
                    hb, wb = rasters[b].mask.shape
                    p0, q0 = i - hb + 1, j - wb + 1
                    r0, c0 = max(0, -p0), max(0, -q0)
                    p1 = min(places.shape[0], i + h)
                    q1 = min(places.shape[1], j + w)
                    if p1 > max(0, p0) and q1 > max(0, q0):
                        places[max(0, p0):p1, max(0, q0):q1] &= ~nofit[r0:r0 + p1 - max(0, p0),
                                                                      c0:c0 + q1 - max(0, q0)]
                placed = True
                break
            if not placed:
                break
            done += 1
        return done

    def finish(self) -> NestedSheet:
        result = NestedSheet(self.sheet, self.placed)
        if self.placed:
            end = max(p.box[2] for p in self.placed) + self.settings.gap
            rest = self.sheet.length - end
            small, large = sorted(self.settings.min_remnant)
            if min(rest, self.sheet.width) >= small and max(rest, self.sheet.width) >= large:
                result.cut_x = end
                result.remnant = StockSheet(self.sheet.material, max(rest, self.sheet.width),
                                            min(rest, self.sheet.width), remnant=True)
        return result


# ── a material ───────────────────────────────────────────────────────────────


def _fits_sheet(part: Part, sheet: StockSheet, settings: Settings) -> bool:
    room_l = sheet.length - 2 * settings.margin
    room_w = sheet.width - 2 * settings.margin
    x0, y0, x1, y1 = part.shape.box
    a, b = x1 - x0, y1 - y0
    turns = part.item.rotate and len(settings.rotations) > 1
    return (a <= room_l and b <= room_w) or (turns and b <= room_l and a <= room_w)


def _angles(part: Part, settings: Settings) -> tuple[float, ...]:
    return tuple(settings.rotations) if part.item.rotate else (0.0,)


def _orders(parts: list[Part], passes: int) -> list[list[Part]]:
    def box_area(p: Part) -> float:
        return p.shape.length * p.shape.width

    orders = [sorted(parts, key=lambda p: (-box_area(p), p.item.designation))]
    if passes > 1:
        orders.append(sorted(parts, key=lambda p: (-p.shape.length, -box_area(p), p.item.designation)))
    if passes > 2:
        orders.append(sorted(parts, key=lambda p: (-p.shape.area, p.item.designation)))
    return orders


def nest_group(material: SheetMaterial, parts: list[Part], stock: list[StockSheet], settings: Settings,
               progress=lambda text: None, available: dict[int, int] | None = None) -> GroupResult:
    """The details of one material on the remnants, the sheets of the list and standard sheets.
    *available* — id(stock sheet) → how many of it are still free; what this group took is
    taken off."""
    available = available if available is not None else {id(s): s.quantity for s in stock}
    sheets: list[StockSheet] = []
    if settings.use_remnants:
        remnants = [s for s in stock if s.remnant and s.material.matches(material, settings.any_grade)
                    and s.free_for(settings.order)]
        for s in sorted(remnants, key=lambda s: (s.area, s.row)):
            sheets += [s] * available.get(id(s), 0)
    whole = [s for s in stock if not s.remnant and s.material.matches(material, settings.any_grade)]
    for s in sorted(whole, key=lambda s: (-s.area, s.row)):
        sheets += [s] * available.get(id(s), 0)
    standard = standard_sheets(material, settings.sheet_sizes)
    group = GroupResult(material)
    usable = []
    for part in parts:
        if any(_fits_sheet(part, s, settings) for s in [*sheets, *standard]):
            usable.append(part)
        else:
            group.too_big.append(part)
    if not usable:
        return group
    best: GroupResult | None = None
    for order in _orders(usable, settings.passes):
        trial = GroupResult(material, too_big=group.too_big)
        left = {id(p): p.item.quantity for p in order}
        cache: dict = {}
        queue = list(sheets)
        while any(left.values()):
            sheet = queue.pop(0) if queue else standard[0]
            nester = SheetNester(sheet, settings, cache)
            for part in order:
                if left[id(part)] and _fits_sheet(part, sheet, settings):
                    left[id(part)] -= nester.place_all(part, left[id(part)], _angles(part, settings))
            if nester.placed:
                trial.sheets.append(nester.finish())
                progress(f"{material.label()}: лист {len(trial.sheets)}, деталей {len(nester.placed)}")
            elif not queue and sheet.unlimited:
                break  # nothing more goes even on a whole sheet
        trial.unplaced = {k: v for k, v in left.items() if v}
        if best is None or _better(trial, best):
            best = trial
    for sheet in best.sheets:
        if id(sheet.stock) in available:
            available[id(sheet.stock)] -= 1
    return best


def _score(group: GroupResult) -> tuple:
    new_sheets = sum(1 for s in group.sheets if not s.stock.remnant)
    used = sum(s.used_area for s in group.sheets)
    return (sum(group.unplaced.values()), new_sheets, used)


def _better(a: GroupResult, b: GroupResult) -> bool:
    return _score(a) < _score(b)


def nest(parts: list[Part], stock: list[StockSheet], settings: Settings | None = None,
         progress=lambda text: None) -> Result:
    """All details with a shape, by material: thickness, grade, plain or checker plate."""
    settings = settings or Settings()
    started = time.monotonic()
    groups: dict[SheetMaterial, list[Part]] = {}
    for part in parts:
        if part.shape is not None and part.item.material is not None and part.item.quantity > 0:
            groups.setdefault(part.item.material, []).append(part)
    results = []
    available = {id(s): s.quantity for s in stock}
    for material in sorted(groups, key=lambda m: (m.thickness, m.checker, m.grade)):
        progress(f"Раскладываю {material.label()}…")
        results.append(nest_group(material, groups[material], stock, settings, progress, available))
    return Result(results, settings, time.monotonic() - started)
