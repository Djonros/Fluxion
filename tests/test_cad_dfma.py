"""«Технологичность» of an assembly (cad/dfma.py): fasteners read from their names, hints to
bring their range down, twin details, sheet details bent too tight — no KOMPAS-3D needed."""
from __future__ import annotations

import pytest

from cad import dfma, report
from cad.assembly import ASSEMBLY, DETAIL, STANDARD, Component
from cad.dfma import parse_fastener
from cad.report import Line

SHEET3 = "Лист$dБ-ПН-О-3 ГОСТ 19903-2015;Ст3сп ГОСТ 14637-89$"


@pytest.fixture(autouse=True)
def _decisions_in_tmp(tmp_path, monkeypatch):
    from cad import workload

    monkeypatch.setattr(report, "decisions_path", lambda: tmp_path / "report_decisions.json")
    monkeypatch.setattr(workload, "norms_path", lambda: tmp_path / "time_norms.json")


def std(name, kg=0.05):
    return Component(STANDARD, "", name, "", kg * 1000, 0, f"C:\\lib\\{name}.m3d")


def det(marking, name, material="Сталь 20 ГОСТ 1050-2013", kg=1.0, m2=0.1, file="", **extra):
    return Component(DETAIL, marking, name, material, kg * 1000, m2 * 10000, file or f"C:\\m\\{marking or name}.m3d",
                     **extra)


def lines(*items):
    """(component, qty on the product) -> report lines."""
    return [Line(c, 0, n) for c, n in items]


@pytest.mark.parametrize("name, expected", [
    ("Болт М12-6gx40.58.016 ГОСТ 7798-70", ("Болт", 12, 0, 40, "ГОСТ 7798-70", "5.8")),
    ("Болт 2М12x1,25-6gx40.88.35Х.019 ГОСТ 7805-70", ("Болт", 12, 1.25, 40, "ГОСТ 7805-70", "8.8")),
    ("Болт М10х30 ГОСТ 7798-70", ("Болт", 10, 0, 30, "ГОСТ 7798-70", "")),
    ("Винт А.М6-6gx16.48.016 ГОСТ 17473-80", ("Винт", 6, 0, 16, "ГОСТ 17473-80", "4.8")),
    ("Шпилька М16-6gx60.58 ГОСТ 22032-76", ("Шпилька", 16, 0, 60, "ГОСТ 22032-76", "5.8")),
    ("Гайка М12-6H.5.016 ГОСТ 5915-70", ("Гайка", 12, 0, 0, "ГОСТ 5915-70", "")),
    ("Гайка М12 ГОСТ ISO 4032-2014", ("Гайка", 12, 0, 0, "ГОСТ ISO 4032-2014", "")),
    ("Шайба 12.01.08кп.016 ГОСТ 11371-78", ("Шайба", 12, 0, 0, "ГОСТ 11371-78", "")),  # 01 — the version
    ("Шайба 12 65Г 019 ГОСТ 6402-70", ("Шайба", 12, 0, 0, "ГОСТ 6402-70", "")),
    ("Штифт 8x40 ГОСТ 3128-70", ("Штифт", 8, 0, 40, "ГОСТ 3128-70", "")),
    ("Болт М8x1-6gx30 ГОСТ 7796-70", ("Болт", 8, 1, 30, "ГОСТ 7796-70", "")),  # a fine pitch without a comma
    ("Болт М8x28 ГОСТ 15591-70", ("Болт", 8, 0, 28, "ГОСТ 15591-70", "")),
    ("Шайба A.30.04 ГОСТ 11371-78", ("Шайба", 30, 0, 0, "ГОСТ 11371-78", "")),
    ("Шайба A 8.37 ГОСТ 6958-78", ("Шайба", 8, 0, 0, "ГОСТ 6958-78", "")),
    ("Шайба 16ОТ 65Г 06 ГОСТ 6402-70", ("Шайба", 16, 0, 0, "ГОСТ 6402-70", "")),
    ("Шайба ГОСТ 32484.5 - 20", ("Шайба", 20, 0, 0, "ГОСТ 32484.5", "")),
])
def test_a_fastener_is_read_from_its_name(name, expected):
    f = parse_fastener(name)
    assert (f.type, f.diameter, f.pitch, f.length, f.standard, f.strength) == expected


def test_what_is_not_a_fastener():
    assert parse_fastener("Колесо 6.5-16") is None
    assert parse_fastener("Подшипник 180205 ГОСТ 8882-75") is None
    assert parse_fastener("") is None


def test_close_lengths_standards_and_classes_are_hints():
    rows, advice = dfma.fasteners(lines(
        (std("Болт М12-6gx35.58.016 ГОСТ 7798-70"), 12),
        (std("Болт М12-6gx40.58.016 ГОСТ 7798-70"), 8),
        (std("Болт М12-6gx40.88.016 ГОСТ 7805-70"), 4),
        (std("Гайка М12-6H.5.016 ГОСТ 5915-70"), 24),
        (std("Болт М20-6gx80.58.016 ГОСТ 7798-70"), 2),
        (std("Болт М20-6gx120.58.016 ГОСТ 7798-70"), 2),  # far apart: no hint
    ))
    assert [(r.type, r.size, r.lengths, r.positions, r.qty) for r in rows] == [
        ("Болт", "М12", (35.0, 40.0), 3, 24), ("Болт", "М20", (80.0, 120.0), 2, 4), ("Гайка", "М12", (), 1, 24)]
    titles = [(a.title, a.message.split(":")[0]) for a in advice]
    assert titles == [("Болты М12", "близкие длины 35, 40 мм"), ("Болты М12", "разные стандарты"),
                      ("Болты М12", "разные классы прочности")]
    assert "35 мм — 12 шт., 40 мм — 12 шт." in advice[0].message and advice[0].qty == 24


def test_washers_of_different_standards_are_different_washers():
    """A flat washer and a spring one under one size are two types, not «разные стандарты»."""
    rows, advice = dfma.fasteners(lines((std("Шайба A.10.04 ГОСТ 11371-78"), 8), (std("Шайба 10Т ГОСТ 6402-70"), 8)))
    assert [(r.size, r.standards, r.qty) for r in rows] == [("10", ("ГОСТ 11371-78",), 8), ("10", ("ГОСТ 6402-70",), 8)]
    assert advice == []


def test_many_lengths_are_a_hint_too():
    _rows, advice = dfma.fasteners(lines(*[(std(f"Болт М16-6gx{l}.58.016 ГОСТ 7798-70"), 1)
                                           for l in (40, 60, 80, 100)]))
    assert [a.message.split(":")[0] for a in advice] == ["4 разных длин"]


def test_twin_details():
    a = det("100.001", "Пластина", kg=2.0, m2=0.2)
    b = det("100.002", "Планка", kg=2.004, m2=0.201)  # the same plate drawn twice
    c = det("100.003", "Косынка", kg=2.5, m2=0.2)
    copy = det("100.001", "Пластина", kg=2.0, m2=0.2)  # the same file met twice is one detail
    advice = dfma.twins(lines((a, 4), (b, 2), (c, 1), (copy, 1)))
    assert len(advice) == 1
    assert advice[0].items == ("100.001 Пластина", "100.002 Планка") and advice[0].qty == 7


def test_executions_of_one_detail_are_not_twins():
    """«-01» is an execution (often the mirrored one): the same mass and area on purpose."""
    a = det("792720.2801312", "Полка нижняя", kg=183.0, m2=2.57)
    b = det("792720.2801312-01", "Полка нижняя", kg=183.0, m2=2.57)
    assert dfma.twins(lines((a, 1), (b, 1))) == []


BENT = ["Эскиз:1", "Листовое тело:1", "Сгиб:1"]


@pytest.mark.parametrize("material, thickness, radius, features, problems", [
    (SHEET3, 3, 3, BENT, []),
    (SHEET3, 3, 1, BENT, [dfma.RADIUS_PROBLEM]),
    (SHEET3, 3, 1, ["Эскиз:1", "Листовое тело:1"], []),  # flat: the radius is only KOMPAS's default
    (SHEET3, 3, 1, ["Сгиб по линии:1"], [dfma.RADIUS_PROBLEM]),
    (SHEET3, 4, 4, BENT, [dfma.THICKNESS_PROBLEM]),
    ("Лист$d2 ГОСТ 21631-76;Д16АТ ГОСТ 4784-2019$", 2, 3, BENT, [dfma.RADIUS_PROBLEM]),  # duralumin: 2.5 t
    ("Лист$d2 ГОСТ 21631-76;АМц ГОСТ 4784-2019$", 2, 1, BENT, []),  # soft: 0.5 t
    (SHEET3, 0, 0, BENT, []),  # not a sheet detail
])
def test_sheet_details(material, thickness, radius, features, problems):
    c = det("200.001", "Уголок", material, sheet_thickness=thickness, bend_radius=radius, features=features)
    assert [p for p, _m in dfma.sheet_problems(c)] == problems


def test_the_report_has_the_advice_and_the_sheet_warnings(tmp_path):
    top = Component(ASSEMBLY, "300.000", "Рама", "", 20000, 0, "C:\\m\\300.000.a3d", children=[
        det("300.001", "Уголок", SHEET3, kg=1.5, m2=0.3, sheet_thickness=3, bend_radius=1, features=BENT),
        std("Болт М12-6gx35.58.016 ГОСТ 7798-70"), std("Болт М12-6gx40.58.016 ГОСТ 7798-70"),
        std("Гайка М12-6H.5.016 ГОСТ 5915-70"), std("Гайка М12-6H.5.016 ГОСТ 5915-70"),
    ])
    r = report.build(top)
    assert [x["problem"] for x in r.remarks] == [dfma.RADIUS_PROBLEM]  # a model error: to be marked
    assert [a.title for a in r.advice] == ["Болты М12"]  # advice: does not hold the report up
    assert "Технологичность: крепёж — 2 типоразмеров" in report.summary_text(r)

    from openpyxl import load_workbook

    path = report.save_xlsx(r, tmp_path / "r.xlsx")
    ws = load_workbook(path)["Технологичность"]
    values = [[c for c in row if c is not None] for row in ws.iter_rows(values_only=True)]
    assert any(row[:2] == ["Крепёж", "Болты М12"] for row in values)
    assert any(row[:2] == ["Гайки", "М12"] and row[-1] == 2 for row in values)


def test_the_sheet_body_is_read_from_kompas():
    """ISheetMetalContainer -> SheetMetalBodies -> Item(0) -> ISheetMetalBody (cast is identity here)."""
    from types import SimpleNamespace

    from cad.assembly import sheet_of

    body = SimpleNamespace(Thickness=3.0, Radius=1.5)
    bodies = SimpleNamespace(Count=1, Item=lambda i: body)
    assert sheet_of(SimpleNamespace(SheetMetalBodies=bodies), lambda obj, iface: obj) == (3.0, 1.5)
    assert sheet_of(SimpleNamespace(SheetMetalBodies=SimpleNamespace(Count=0)), lambda o, i: o) == (0.0, 0.0)

    def no_sheet(obj, iface):
        raise TypeError("not a sheet detail")

    assert sheet_of(SimpleNamespace(), no_sheet) == (0.0, 0.0)


def test_mark_all_such_in_the_warnings_window():
    """Many details bent too tight: one click marks every row of that remark."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from desktop_browser.app import ReportWarningsDialog
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    bent = [det(f"400.00{i}", "Уголок", SHEET3, kg=1.0 + i, m2=0.1 + i, sheet_thickness=3, bend_radius=1,
                features=BENT) for i in range(3)]
    top = Component(ASSEMBLY, "400.000", "Рама", "", 9000, 0, "C:\m\400.000.a3d",
                    children=bent + [det("", "Планка", kg=1.0, m2=0.1)])
    dialog = ReportWarningsDialog(report.build(top))
    radius_rows = dialog.same_rows(next(i for i, x in enumerate(dialog.result.remarks)
                                        if x["problem"] == dfma.RADIUS_PROBLEM))
    assert len(radius_rows) == 3
    dialog.table.setCurrentCell(radius_rows[0], 3)
    assert dialog.same_button.isEnabled() and dialog.same_button.text() == "Отметить все такие (3)"
    dialog.same_button.click()
    assert all(dialog.table.item(i, 0).checkState() == Qt.Checked for i in radius_rows)
    assert not dialog.make_button.isEnabled()  # «Деталь без обозначения» is still to mark
    assert dialog.mark_same(radius_rows[0], checked=False) == 3
    assert all(dialog.table.item(i, 0).checkState() == Qt.Unchecked for i in radius_rows)


def test_the_laser_package_takes_the_thickness_of_the_material():
    """«Планка наката»: a sheet body of 1040 mm under «Лист 10» goes to the 10 mm folder."""
    from dxfka.utils import choose_thickness

    assert choose_thickness("1040", "10") == "10"
    assert choose_thickness("3", "") == "3"  # a sheet body under a solid material
    assert choose_thickness("", "8") == "8"  # a plate cut from a sheet
    assert choose_thickness("", "") == ""
