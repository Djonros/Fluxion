"""Welds from the joints of details (cad/welds.py): geometry on made-up boxes."""
from __future__ import annotations

import pytest

from cad import report, welds
from cad.assembly import ASSEMBLY, DETAIL, STANDARD, Component


def box(x0, y0, z0, x1, y1, z1, flip=False):
    """Six faces of a box; *flip* turns the normals inwards (KOMPAS gives surface normals)."""
    s = -1 if flip else 1
    p = lambda x, y, z: (float(x), float(y), float(z))  # noqa: E731
    return [
        welds.Face((0, 0, -s), [p(x0, y0, z0), p(x1, y0, z0), p(x1, y1, z0), p(x0, y1, z0)]),
        welds.Face((0, 0, s), [p(x0, y0, z1), p(x1, y0, z1), p(x1, y1, z1), p(x0, y1, z1)]),
        welds.Face((0, -s, 0), [p(x0, y0, z0), p(x1, y0, z0), p(x1, y0, z1), p(x0, y0, z1)]),
        welds.Face((0, s, 0), [p(x0, y1, z0), p(x1, y1, z0), p(x1, y1, z1), p(x0, y1, z1)]),
        welds.Face((-s, 0, 0), [p(x0, y0, z0), p(x0, y1, z0), p(x0, y1, z1), p(x0, y0, z1)]),
        welds.Face((s, 0, 0), [p(x1, y0, z0), p(x1, y1, z0), p(x1, y1, z1), p(x1, y0, z1)]),
    ]


WALL = box(0, 0, 0, 1000, 500, 10)  # a plate 1000×500, 10 thick, lying on z = 0..10


def test_rib_on_a_wall_is_welded_on_both_sides():
    rib = box(100, 0, 10, 110, 300, 210)  # 10 thick, 300 long, standing on the wall
    assert welds.joint_length(WALL, rib) == pytest.approx(2 * (300 + 10))


def test_pad_on_a_plate_is_welded_all_around_whatever_the_normals():
    pad = box(200, 100, 10, 300, 150, 18, flip=True)
    assert welds.joint_length(WALL, pad) == pytest.approx(2 * (100 + 50))


def test_overhanging_pad_counts_only_the_common_area():
    pad = box(950, 100, 10, 1100, 150, 18)  # half of it beyond the edge of the wall
    assert welds.joint_length(WALL, pad) == pytest.approx(2 * (50 + 50))


def test_butt_joint_of_end_faces():
    # two plates end to end: the seam goes around the common end face
    assert welds.joint_length(WALL, box(1000, 0, 0, 1100, 100, 10)) == pytest.approx(2 * (100 + 10))


def test_no_joint():
    assert welds.joint_length(WALL, box(0, 0, 11, 100, 100, 20)) == 0  # 1 mm gap
    assert welds.joint_length(WALL, box(1000, 0, 10, 1100, 100, 20)) == 0  # along an edge only: no common area
    assert welds.joint_length(WALL, box(2000, 0, 10, 2100, 100, 20)) == 0  # same plane, far away


def test_transform_by_placement_matrix():
    m = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -90, 5, 0, 1)  # KOMPAS: translation in the last row
    assert welds.transform(m, (10, 0, 0)) == (-80, 5, 0)
    rot = (0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)  # 90° about Z
    assert welds.rotate(rot, (1, 0, 0)) == (0, 1, 0)


@pytest.mark.parametrize("material,thickness", [
    ("Лист$dБ-ПУ-О-10 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$", 10),
    ("Лист$d8 ГОСТ 19903-2015;10ХСНД ГОСТ 19281-2014$", 8),
    ("Лист$d1,5 ГОСТ 19904-90;08пс ГОСТ 16523-97$", 1.5),
    ("120х120х8-ГОСТ 30245-2012", 8),
    ("Труба$d57х3.5 ГОСТ 8732-78;20 ГОСТ 8731-74$", 3.5),
    ("Сталь 10 ГОСТ 1050-2013", None),
    ("", None),
])
def test_thickness_from_sortament(material, thickness):
    assert welds.thickness_from_material(material) == thickness


def test_leg_is_the_thinner_detail_but_at_most_12():
    assert welds.leg_for(10, 8) == 8
    assert welds.leg_for(30, 20) == 12
    assert welds.leg_for(None, 6) == 6
    assert welds.leg_for(28.50132633, None) == 12
    assert welds.leg_for(None, None) == 0


def test_weld_mass():
    # leg 10: 50 mm² of triangle × 1000 mm × 7.85 g/cm³ × 1.2 = 471 g
    assert welds.weld_mass_g(10, 1000) == pytest.approx(471.0)


def _det(name, material="Сталь 20 ГОСТ 1050-2013"):
    return Component(DETAIL, "", name, material, 1000, 0, f"C:\\m\\{name}.m3d")


def _asm(marking, children, file=None):
    return Component(ASSEMBLY, marking, marking, "", 0, 0, file or f"C:\\m\\{marking}.a3d", children=children)


def test_welded_assemblies_have_no_fasteners():
    bolt = Component(STANDARD, "", "Болт", "", 50, 0, "C:\\lib\\bolt.m3d")
    welded = _asm("1.100", [_det("a"), _det("b")])
    bolted = _asm("1.200", [_det("c"), _det("d"), bolt, bolt])
    one_detail = _asm("1.300", [_det("e")])
    top = _asm("1.000", [welded, welded, bolted, one_detail, _asm("1.400", [welded])])
    assert welds.is_welded(welded) and not welds.is_welded(bolted) and not welds.is_welded(one_detail)
    assert [(c.marking, n) for c, n in welds.welded_assemblies(top)] == [("1.100", 3)]
    installation = Component(ASSEMBLY, "1.500", "Установка настила", "", 0, 0, r"C:\m\1.500.a3d",
                             children=[_det("панель"), _det("панель 2")])
    assert not welds.is_welded(installation)


def test_axles_and_details_without_material_are_not_welded():
    axle = Component(ASSEMBLY, "", "Ось 35т квадрат 150мм", "", 0, 0, r"C:\m\axle.a3d",
                     children=[_det("a"), _det("b")])
    half = Component(ASSEMBLY, "", "полуось барабан", "", 0, 0, r"C:\m\half.a3d", children=[_det("a"), _det("b")])
    defaults = _asm("1.600", [_det("a", "Сталь 10 ГОСТ 1050-2013"), _det("b", "")])
    mixed = _asm("1.700", [_det("a"), _det("b"), _det("c", "Без указания материала")])
    assert not welds.is_welded(axle) and not welds.is_welded(half) and not welds.is_welded(defaults)
    assert welds.is_welded(mixed)
    assert not welds.welded_detail(mixed.children[2])


def test_identical_joints_are_one_line():
    joints = welds.group_joints([("Стенка", "Ребро", 10, 1094.2), ("Ребро", "Стенка", 10, 1093.8),
                                 ("Стенка", "Пластина", 8, 326)])
    assert [(j.a, j.b, j.count) for j in joints] == [("Ребро", "Стенка", 2), ("Пластина", "Стенка", 1)]


def test_weld_sheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    welded = _asm("1.100", [_det("a"), _det("b")])
    r = report.build(_asm("1.000", [welded, welded]))
    w = welds.WeldedAssembly(welded, 2, welds.group_joints([("a", "b", 10, 1000)]))
    r.welds = [w]
    assert r.weld_length_m == pytest.approx(2.0) and r.weld_mass_kg == pytest.approx(0.942)
    assert "Сварка: сварных сборочных единиц 1" in report.summary_text(r)
    wb = openpyxl.load_workbook(report.save_xlsx(r, tmp_path / "r.xlsx"))
    rows = list(wb["Сварка"].iter_rows(values_only=True))
    assert any(row[0] == "Итого на изделие" and row[7] == pytest.approx(2.0) for row in rows)
    assert any(row[0] == "Таблица сварки по катетам" for row in rows)


def test_add_welds_never_breaks_the_report(monkeypatch):
    def boom(top, log=print):
        raise RuntimeError("КОМПАС недоступен")

    monkeypatch.setattr(welds, "compute", boom)
    r = report.build(_asm("1.000", [_det("a")]))
    lines = []
    report.add_welds(r, log=lines.append)
    assert r.welds == [] and "КОМПАС недоступен" in lines[0]
