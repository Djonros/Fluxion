"""Sheet-metal geometry: a bent profile from flange lengths, its flat length, holes in flanges.

A profile is a chain of straight flanges joined by bends, drawn in the XOZ
plane and extruded along +Y by the width of the part.  KOMPAS builds a sheet
body from an open sketch line that is one face of the sheet; here it is the
face on the left of the direction of travel, and the material lies to the
right (KOMPAS ThicknessDirection = True).  The bend radius is the inner one
at every bend, whichever face the sketch runs on (checked on KOMPAS v24).

Flange lengths are outside sizes by default: up to the virtual sharp of the
outer faces at a bend, or to the free edge.  ``inside=True`` takes them up to
the sharp of the inner faces instead.

Flat length = sum of the straight parts + sum of the bend arcs on the neutral
layer, φ·(r + K·t) for a bend by φ radians.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Neutral layer factor K (Романовский) by r/t, for bending by 90° and close to it
K_TABLE = [(0.1, 0.21), (0.2, 0.22), (0.3, 0.23), (0.4, 0.24), (0.5, 0.25), (0.6, 0.26), (0.7, 0.28),
           (0.8, 0.30), (1.0, 0.32), (1.2, 0.33), (1.5, 0.36), (2.0, 0.37), (2.5, 0.38), (3.0, 0.40),
           (4.0, 0.42), (5.0, 0.44), (6.0, 0.46), (7.0, 0.48), (8.0, 0.50)]


def k_factor(radius: float, thickness: float) -> float:
    """K by the table for r/t (linear between rows, the end rows outside)."""
    ratio = radius / thickness
    if ratio <= K_TABLE[0][0]:
        return K_TABLE[0][1]
    for (r1, k1), (r2, k2) in zip(K_TABLE, K_TABLE[1:]):
        if ratio <= r2:
            return round(k1 + (k2 - k1) * (ratio - r1) / (r2 - r1), 3)
    return K_TABLE[-1][1]


class ProfileError(ValueError):
    """The flanges cannot be bent as asked (too short for the radius and so on)."""


@dataclass
class Profile:
    points: list[tuple[float, float]]   # sketch line (left face), XOZ coordinates
    directions: list[tuple[float, float]]
    straights: list[float]              # flat (tangent-to-tangent) length of each flange
    bends: list[float]                  # bend angles φ, radians (positive: turn left)
    thickness: float
    radius: float
    k: float

    @property
    def arcs(self) -> list[float]:
        return [abs(phi) * (self.radius + self.k * self.thickness) for phi in self.bends]

    @property
    def flat_length(self) -> float:
        return sum(self.straights) + sum(self.arcs)

    def flange_span(self, index: int) -> tuple[float, float]:
        """Distances along flange *index* (from its first sketch point) where its flat part starts and ends."""
        length = math.dist(self.points[index], self.points[index + 1])
        start = self._setback(index - 1) if index > 0 else 0.0
        end = length - (self._setback(index) if index < len(self.bends) else 0.0)
        return start, end

    def _setback(self, bend: int) -> float:
        """From the virtual sharp of the sketch face to where bend *bend* starts."""
        phi = self.bends[bend]
        inner = phi > 0  # a left turn has the sketch (left) face inside the bend
        return (self.radius if inner else self.radius + self.thickness) * math.tan(abs(phi) / 2)

    def point_on(self, index: int, distance: float, depth: float = 0.0) -> tuple[float, float]:
        """XOZ point on flange *index* at *distance* from its start, *depth* into the material."""
        (x0, z0), (dx, dz) = self.points[index], self.directions[index]
        return x0 + dx * distance + dz * depth, z0 + dz * distance - dx * depth


def build_profile(lengths: list[float], turns: list[float], thickness: float, radius: float, k: float,
                  inside: bool = False, start_direction: float = 0.0) -> Profile:
    """Flanges *lengths* joined by bends *turns* (degrees, + left / − right).

    *start_direction* is the direction of the first flange, degrees from +X
    towards +Z.  The sketch line is shifted so that the profile's outer
    faces start at X = 0 and Z = 0 (for 90° bends).
    """
    if len(turns) != len(lengths) - 1:
        raise ProfileError("число сгибов должно быть на один меньше числа полок")
    t, r = thickness, radius
    phis = [math.radians(a) for a in turns]
    # length of each flange along the sketch (left) face, between its virtual sharps
    face = []
    for i, length in enumerate(lengths):
        value = length
        for j in (i - 1, i):
            if 0 <= j < len(phis):
                shift = t * math.tan(abs(phis[j]) / 2)
                left_inner = phis[j] > 0
                if not inside and left_inner:
                    value -= shift   # outside size, the sketch face is the inner one here
                elif inside and not left_inner:
                    value += shift   # inside size, the sketch face is the outer one here
        face.append(value)
    angle = math.radians(start_direction)
    directions, points = [], [(0.0, 0.0)]
    for i, length in enumerate(face):
        d = (math.cos(angle), math.sin(angle))
        directions.append(d)
        x, z = points[-1]
        points.append((x + d[0] * length, z + d[1] * length))
        if i < len(phis):
            angle += phis[i]
    profile = Profile(points, directions, [], phis, t, r, k)
    straights = []
    for i in range(len(face)):
        start, end = profile.flange_span(i)
        straights.append(end - start)
    profile.straights = straights
    # the outer faces at X = 0 and Z = 0: the material is on the right of the sketch line
    xs = [p[0] for p in points]
    zs = [p[1] for p in points]
    corners = [profile.point_on(i, s, t) for i in range(len(face))
               for s in (0.0, math.dist(points[i], points[i + 1]))]
    dx = -min(xs + [c[0] for c in corners])
    dz = -min(zs + [c[1] for c in corners])
    profile.points = [(round(x + dx, 6), round(z + dz, 6)) for x, z in points]
    return profile
