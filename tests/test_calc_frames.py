"""Second stage of the calculator: the trailer frame as a grillage, lateral-torsional buckling,
compressed members, local stresses and the welds of the frame joints — against hand calculations."""
from __future__ import annotations

import math

import pytest

from calc import beams, columns, frames, sections, stability
from calc.core import CalcError

G = 9.80665
E = 206_000.0


def check(result, title):
    return next(c for c in result.checks if c.title.startswith(title))


def solve(raw: dict):
    """The grillage of *raw* without the checks: (forces by member, the grid)."""
    v = frames.read(frames.FIELDS, raw).values
    profiles = {frames.SPAR: sections.parse(v["spar"]), frames.CROSS: sections.parse(v["cross"])}
    profiles[frames.DRAWBAR] = profiles[frames.SPAR]
    builder, supports, point, _, _, _ = frames._geometry(v, profiles)
    hold = supports if v["joints"] == frames.HINGED else []
    grid = frames.solve(builder.nodes, builder.elements, supports, point, E, hold)
    return frames.member_forces(grid), grid


BASE = {"length": 6000, "width": 1000, "axles": "0; 6000", "crossmembers": "0; 6000", "spar": "швеллер 16",
        "cross": "труба 60х40х3", "self_weight": "нет"}


# ── sections: torsion ───────────────────────────────────────────────────────

def test_torsion_constants():
    pipe = sections.parse("труба 57х3,5")
    assert pipe.j == pytest.approx(2 * pipe.ix) and pipe.wt == pytest.approx(pipe.j / 28.5)
    tube = sections.parse("труба 80х40х3")     # Bredt: J = 4·Am²·t/s by the centre line of the wall
    am = 77 * 37 - (4 - math.pi) * 4.5 ** 2
    s = 2 * (77 + 37) - (8 - 2 * math.pi) * 4.5
    assert tube.j == pytest.approx(4 * am ** 2 * 3 / s) and tube.wt == pytest.approx(2 * am * 3)
    assert not tube.open and tube.curve == "a"
    beam = sections.parse("двутавр 20")
    assert beam.iw == pytest.approx(115e4 * (200 - 8.4) ** 2 / 4) and beam.open and beam.curve == "b"
    assert sections.parse("швеллер 16").curve == "c" and not sections.parse("швеллер 16 плашмя").open


# ── stability ───────────────────────────────────────────────────────────────

def test_critical_moment_by_hand():
    beam = sections.parse("двутавр 20")
    L, Gm = 6000, E / 2.6
    euler = math.pi ** 2 * E * beam.iy / L ** 2
    expected = 1.35 * euler * math.sqrt(beam.iw / beam.iy + L ** 2 * Gm * beam.j / (math.pi ** 2 * E * beam.iy))
    assert stability.critical_moment(beam, L, E, 1.35) == pytest.approx(expected)
    assert expected / 1e6 == pytest.approx(24.7, abs=0.1)
    phi1, phib = stability.phi_b(beam, L, E, 255, 1.35)
    assert phi1 == pytest.approx(expected / (beam.wx * 255)) and phib == phi1      # φ1 ≤ 0,85
    short1, short_b = stability.phi_b(beam, 1000, E, 255)
    assert short1 > 0.85 and short_b == pytest.approx(min(1.0, 0.68 + 0.21 * short1))
    channel1, _ = stability.phi_b(sections.parse("двутавр 20"), 3000, E, 255)
    assert stability.phi_b(sections.parse("швеллер 20"), 3000, E, 255)[0] < channel1


def test_phi_of_sp16():
    # СНиП II-23-81*, табл. 72: λ = 70, Ry = 240 МПа -> φ = 0,754 (тип b в СП 16)
    lam_bar = 70 * math.sqrt(240 / E)
    assert stability.phi(lam_bar, "b") == pytest.approx(0.754, abs=0.01)
    assert stability.phi(0.1, "a") == 1.0
    assert stability.phi(2.0, "a") > stability.phi(2.0, "b") > stability.phi(2.0, "c")
    assert stability.phi(6.0, "b") <= 7.6 / 36


def test_column():
    r = columns.calculate({"force": "100 кН", "length": "2 м", "profile": "труба 60х60х4"})
    sec = sections.parse("труба 60х60х4")
    lam = 2000 / sec.i_min
    phi = stability.phi(lam * math.sqrt(255 / E), "a")
    assert check(r, "Устойчивость стержня").actual == pytest.approx(100_000 / (phi * sec.area))
    console = columns.calculate({"force": "100 кН", "length": "2 м", "profile": "труба 60х60х4",
                                 "scheme": "заделка — свободный конец"})
    assert console.margin < r.margin and any("закрепить свободный конец" in s for s in console.suggestions)
    assert any("больше 120" in n for n in console.notes)
    bent = columns.calculate({"force": "50 кН", "length": "1 м", "profile": "труба 60х60х4", "moment": "1 кН·м"})
    c = check(bent, "Устойчивость при сжатии с изгибом")
    lam = 1000 / sec.i_min
    phi = stability.phi(lam * math.sqrt(255 / E), "a")
    assert c.actual == pytest.approx(50_000 / (phi * sec.area * 255) + 1e6 / (sec.wx * 255))


def test_beam_lateral_buckling_and_local_stress():
    r = beams.calculate({"length": "6 м", "loads": "10 кН в 3000", "profile": "двутавр 20", "lateral": 6000,
                         "bearing": 100, "self_weight": "нет"})
    sec = sections.parse("двутавр 20")
    phib = stability.phi_b(sec, 6000, E, 255, 1.35)[1]
    c = check(r, "Общая устойчивость")
    assert c.limit == pytest.approx(phib * sec.wx * 255 / 1e6) and c.actual == pytest.approx(15.0)
    local = check(r, "Местное напряжение")
    assert local.actual == pytest.approx(10_000 / (5.2 * (100 + 2 * 8.4)))
    assert beams.c1_of([beams.Load("line", 1, 0, 1)])[0] == 1.13
    assert beams.c1_of([beams.Load("moment", 1, 0, 0)])[0] == 1.0
    tube = beams.calculate({"length": "6 м", "loads": "10 кН в 3000", "profile": "труба 100х50х4", "lateral": 6000})
    assert not any(c.title.startswith("Общая") for c in tube.checks)


# ── the frame as a grillage ─────────────────────────────────────────────────

def test_symmetric_frame_is_two_simple_beams():
    q_total = 60_000.0      # 60 кН on the deck -> 5 Н/мм on each of two spars
    forces, grid = solve({**BASE, "body_load": "60 кН"})
    for spar in ("Лонжерон левый", "Лонжерон правый"):
        assert forces[spar].moment == pytest.approx(5 * 6000 ** 2 / 8, rel=1e-3)
    assert forces["Поперечина 0"].moment == pytest.approx(0, abs=1)
    assert sum(grid.reactions.values()) == pytest.approx(q_total)
    w_mid = max(grid.w)
    assert w_mid == pytest.approx(5 * 5 * 6000 ** 4 / (384 * E * 747e4), rel=1e-3)


def test_frame_statics_and_load_sharing():
    raw = {**BASE, "axles": "1500; 4500", "crossmembers": "0; 3000; 6000", "loads": "20 кН в 3000 слева"}
    forces, grid = solve(raw)
    assert sum(grid.reactions.values()) == pytest.approx(20_000)
    # rigid joints: the cross-members pass part of the load to the right spar
    assert 0 < forces["Лонжерон правый"].moment < forces["Лонжерон левый"].moment
    hinged, grid_h = solve({**raw, "joints": "шарнир", "crossmembers": "0; 6000"})
    # hinged cross-members only at the ends, far from the load: the left spar takes it as a simple beam on the axles
    assert hinged["Лонжерон левый"].moment == pytest.approx(20_000 * 3000 / 4, rel=0.02)
    assert hinged["Лонжерон правый"].moment < 0.02 * hinged["Лонжерон левый"].moment


def test_cross_member_between_fixed_and_pinned():
    P, B = 10_000.0, 1000.0
    forces, _ = solve({**BASE, "spar": "труба 120х80х5", "crossmembers": "0; 3000; 6000", "loads": "10 кН в 3000"})
    m = forces["Поперечина 3000"].moment
    assert P * B / 8 < m < P * B / 4
    soft, _ = solve({**BASE, "spar": "швеллер 16", "crossmembers": "0; 3000; 6000", "loads": "10 кН в 3000"})
    assert soft["Поперечина 3000"].moment > m        # a channel twists easily: the cross-member is nearer to pinned


def test_single_axle_needs_the_coupling():
    raw = {**BASE, "axles": "3500", "crossmembers": "0; 3500; 6000", "drawbar": 1500, "body_load": "20 кН"}
    with pytest.raises(CalcError, match="сцепк"):
        frames.calculate(raw)
    r = frames.calculate({**raw, "coupling": "да"})
    reactions = {row[0]: float(row[1].replace(",", ".")) for row in r.tables[1][2]}
    # moments about the axle: the coupling at −1500 holds the part of 20 кН (centre at 3000) ahead of the axle
    assert reactions["сцепка"] == pytest.approx(20 * (3500 - 3000) / (3500 + 1500), rel=0.02)
    assert sum(reactions.values()) == pytest.approx(20, rel=0.01)


def test_frame_checks_and_suggestions():
    raw = {"length": "6 м", "width": 1000, "axles": "1500; 4500", "crossmembers": "0; 1500; 3000; 4500; 6000",
           "drawbar": 1500, "spar": "швеллер 16", "cross": "труба 60х40х3", "body_load": "3 тс", "loads": "1 тс в 3000",
           "kd": 1.5, "weld_leg": 4}
    r = frames.calculate(raw)
    weak = check(r, "Поперечина 3000 (труба 60х40х3): прочность")
    assert weak.margin < 1 and r.suggestions[0].startswith("поперечины — труба")
    # the cross-member under 1 тс in its middle works about as a simple beam: σ ≈ kд·P·B/4/Wx
    assert weak.actual == pytest.approx(1.5 * G * 1000 * 1000 / 4 / sections.parse("труба 60х40х3").wx, rel=0.05)
    better = r.suggestions[0].split("— ")[1].split(":")[0]
    fixed = frames.calculate({**raw, "cross": better})
    assert all(c.margin >= c.required for c in fixed.checks if c.title.startswith("Поперечина"))
    assert any(c.title.startswith("Лонжерон левый: общая устойчивость (lef 1500") for c in r.checks)
    assert any("сварка к лонжерону" in c.title for c in r.checks)
    assert r.diagram["type"] == "frame" and len(r.diagram["supports"]) == 4


def test_joint_weld_stress_by_hand():
    sec = sections.parse("труба 60х40х3")
    a = 0.7 * 4
    area = a * 2 * (60 + 40)
    ix = a * (2 * 40 * 30 ** 2 + 60 ** 3 / 6)
    iy = a * (2 * 60 * 20 ** 2 + 40 ** 3 / 6)
    tau = frames.joint_weld_stress(sec, 4, 5000, 200_000, 100_000)
    assert tau == pytest.approx(5000 / area + 200_000 / (ix / 30) + 100_000 * math.hypot(30, 20) / (ix + iy))


def test_point_loads_and_errors():
    assert frames.parse_point_loads("1 тс в 2000; 500 кгс в 3 м слева; 2 кН в 1000 справа") == [
        (pytest.approx(G * 1000), 2000, "center"), (pytest.approx(500 * G), 3000, "left"), (2000, 1000, "right")]
    with pytest.raises(CalcError, match="груз"):
        frames.parse_point_loads("тяжёлый ящик")
    with pytest.raises(CalcError, match="вне рамы"):
        frames.calculate({**BASE, "axles": "7000"})
    r = frames.calculate({**BASE, "loads": "1 тс в 2000"})
    assert any("поделён поровну" in n for n in r.notes)
    # cross-members only at the ends: the spars of channels lose their stability over 6 m
    assert check(r, "Лонжерон левый: общая устойчивость (lef 6000").margin < 1


def test_banded_solver():
    import numpy as np

    from calc import linear

    rng = np.random.default_rng(1)
    n, bw = 60, 5
    a = np.zeros((n, n))
    for i in range(n):
        for j in range(max(0, i - bw), min(n, i + bw + 1)):
            a[i, j] = a[j, i] = rng.uniform(-1, 1)
    a += np.eye(n) * 20
    b = rng.uniform(-1, 1, n)
    assert linear.bandwidth(a) == bw
    assert np.allclose(linear.solve(a, b), np.linalg.solve(a, b))
    with pytest.raises(linear.Singular):
        linear.solve(np.array([[1.0, 1.0], [1.0, 1.0]]), np.array([1.0, 1.0]))


def test_cross_member_step():
    assert frames.cross_positions("через 1500", 6000) == [0, 1500, 3000, 4500, 6000]
    assert frames.cross_positions("шаг 1 м", 2500) == [0, 1000, 2000, 2500]
    assert frames.cross_positions("0; 3 м", 6000) == [0, 3000]
