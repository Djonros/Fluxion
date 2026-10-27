"""Stability: lateral-torsional buckling of beams and buckling of compressed members; local stresses.

Lateral-torsional buckling of an open section (I-beam, channel, bent channel)
between the restraints of its compressed flange, lef apart:
    Mcr = C1·(π²·E·Iy/lef²)·√(Iw/Iy + lef²·G·J/(π²·E·Iy))     (Тимошенко; C1 by the moment diagram:
          1,0 a uniform moment, 1,13 a uniform load, 1,35 a force in the middle)
    φ1 = Mcr/(Wx·σт);  φb = φ1 at φ1 ≤ 0,85, else 0,68 + 0,21·φ1 ≤ 1      (СП 16.13330, прил. Ж)
    a channel — φ1 halved, as СП 16 (Ж.3) does for channels: the load is off its shear centre
    the limit is φb·Wx·σт against kд·M.  Closed tubes, strips bent on edge are not checked.
Buckling of a compressed member (СП 16.13330.2017, п. 7.1.3):
    λ = μ·l/i,  λ̄ = λ·√(σт/E),  δ = 9,87·(1 − α + β·λ̄) + λ̄²,
    φ = 0,5·(δ − √(δ² − 39,48·λ̄²))/λ̄² ≤ 7,6/λ̄²,  α, β by the curve: a 0,03 0,06; b 0,04 0,09; c 0,04 0,14
Local stress in the web under a concentrated force or a support (СП 16, п. 8.2.2):
    σloc = F/(tw·lef),  lef = b + 2·tf  (b — the length the force is spread over)
"""
from __future__ import annotations

import math

from .sections import Section

C1 = {"равномерный момент": 1.0, "распределённая нагрузка": 1.13, "сила в середине": 1.35}
CURVES = {"a": (0.03, 0.06), "b": (0.04, 0.09), "c": (0.04, 0.14)}
MU = {"шарнир — шарнир": 1.0, "заделка — свободный конец": 2.0, "заделка — шарнир": 0.7, "заделка — заделка": 0.5}


def critical_moment(section: Section, lef: float, elastic: float, c1: float = 1.13) -> float:
    """Mcr, N·mm, of an open section between lateral restraints *lef* mm apart."""
    shear_modulus = elastic / 2.6
    euler = math.pi ** 2 * elastic * section.iy / lef ** 2
    return c1 * euler * math.sqrt(section.iw / section.iy + lef ** 2 * shear_modulus * section.j
                                  / (math.pi ** 2 * elastic * section.iy))


def phi_b(section: Section, lef: float, elastic: float, yield_: float, c1: float = 1.13) -> tuple[float, float]:
    """(φ1, φb) for the lateral-torsional buckling."""
    phi1 = critical_moment(section, lef, elastic, c1) / (section.wx * yield_)
    if section.family != "i_beam":
        phi1 *= 0.5
    phib = phi1 if phi1 <= 0.85 else min(1.0, 0.68 + 0.21 * phi1)
    return phi1, phib


def slenderness(section: Section, length: float, mu: float, yield_: float, elastic: float) -> tuple[float, float]:
    """(λ, λ̄) about the weak axis."""
    lam = mu * length / section.i_min
    return lam, lam * math.sqrt(yield_ / elastic)


def phi(lam_bar: float, curve: str) -> float:
    """The buckling factor φ of СП 16 (7.1.3) for the conditional slenderness λ̄."""
    if lam_bar <= 0.0:
        return 1.0
    alpha, beta = CURVES[curve]
    delta = 9.87 * (1 - alpha + beta * lam_bar) + lam_bar ** 2
    value = 0.5 * (delta - math.sqrt(delta ** 2 - 39.48 * lam_bar ** 2)) / lam_bar ** 2
    return min(1.0, value, 7.6 / lam_bar ** 2)


def local_length(section: Section, bearing: float) -> float:
    """lef of a concentrated force spread over *bearing* mm of the flange."""
    return bearing + 2 * section.tf


def local_stress(section: Section, force: float, bearing: float) -> float:
    """σloc, MPa: the force through the web(s) — two walls of a tube, the web of a channel or an I-beam."""
    return force / (section.web * local_length(section, bearing))
