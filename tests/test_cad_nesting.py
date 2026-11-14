"""«Раскрой листа» (cad/nesting): the order and the remnants as the plant keeps them, the DXF
of a designation, the nesting of the true shape, the files of the result."""
from __future__ import annotations

import itertools
import math
import os

import pytest

np = pytest.importorskip("numpy")
openpyxl = pytest.importorskip("openpyxl")
ezdxf = pytest.importorskip("ezdxf")

from cad.nesting import (
    Settings,
    engine,
    materials,
    orders,
    output,
    parts,
    prepare,
    run,
    stock,
)

# ── materials ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, thickness, grade, checker", [
    ("Лист 8 (09Г2С)", 8, "09Г2С", False),
    ("Лист 3 (3пс) чечевица", 3, "СТ3", True),
    ("4 Чеч-цаСт3", 4, "СТ3", True),
    ("1,5 Ст3", 1.5, "СТ3", False),
    ("8  10ХСНД", 8, "10ХСНД", False),
    ("8 Чеч-ца", 8, "", True),
    ("Лист 6 ГОСТ 19903-2015 / 09Г2С ГОСТ 19281-2014", 6, "09Г2С", False),
    ("Лист$d8 ГОСТ 19903-2015/09Г2C", 8, "09Г2С", False),  # a Latin C in the grade
    ("AISI 430 2мм", 2, "AISI430", False),
])
def test_sheet_material_of_a_cell(text, thickness, grade, checker):
    m = materials.parse(text)
    assert (m.thickness, m.grade, m.checker) == (thickness, grade, checker)


def test_material_matching():
    st3 = materials.parse("Лист 3 (3пс)")
    assert st3.matches(materials.parse("3 Ст3"))
    assert not st3.matches(materials.parse("3 Чеч-цаСт3"))  # checker plate apart
    assert not materials.parse("8 09Г2С").matches(materials.parse("8 10ХСНД"))
    assert materials.parse("8 09Г2С").matches(materials.parse("8 10ХСНД"), any_grade=True)
    assert not materials.parse("8 Чеч-ца").matches(materials.parse("Лист 8 (Ст3) чечевица"))  # no grade: no match
    assert materials.parse("Нет толщины") is None
    assert materials.parse("Лист 3 (3пс) чечевица").label() == "3 Ст3 чечевица"


# ── the order ────────────────────────────────────────────────────────────────


def make_order(path, rows, header=("№ \nп/п", "Обозначение", "Наименование", "Кол-во\nна 1 шт.", "Кол-во\nна 5 шт.",
                                   "Масса", "Материал")):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Детали"
    sheet.append(list(header))
    for i, (designation, name, qty, material) in enumerate(rows, start=1):
        sheet.append([i, designation, name, qty, f"=D{i + 1}*5", 1.0, material])
    sheet.append([None, None, None, None, None, 999.0, None])  # the total row
    book.save(path)
    return path


def test_order_as_the_plant_keeps_it(tmp_path):
    path = make_order(tmp_path / "762000-2шт. - Иждрил - плазма - рама.xlsx", [
        ("544000.2801121-14", "Ребро большое", 2, "Лист 8 (09Г2С)"),
        ("544000.2801124-12", "Обечайка (Г в св)", 4, "Лист 8 (09Г2С)"),
        (3519090, "Кронштейн", 1, "Лист 6 (09Г2С)"),
        ("762000.2801082", "Поперечина N°1 (Г на стороне)", 1, "Лист 8 (09Г2С)"),
        ("544000.0000001", "Без количества", None, "Лист 8 (09Г2С)"),
        ("544000.0000002", "Плохой материал", 1, "Труба"),
    ])
    order = orders.read_order(path)
    assert order.number == "762000" and order.products == 2
    by = {i.designation: i for i in order.items}
    assert by["544000.2801121-14"].quantity == 4 and by["544000.2801121-14"].per_product == 2
    assert by["3519090"].material.thickness == 6  # a number cell is a designation too
    assert by["544000.2801124-12"].bent and by["762000.2801082"].bent and not by["544000.2801121-14"].bent
    assert "544000.0000001" not in by
    assert by["544000.0000002"].material is None
    assert any("Без количества" in p or "544000.0000001" in p for p in order.problems)
    assert any("Труба" in p for p in order.problems)
    assert [m.label() for m in order.materials()] == ["6 09Г2С", "8 09Г2С"]
    assert orders.read_order(path, products=5).items[0].quantity == 10


def test_order_with_one_quantity_and_own_columns(tmp_path):
    path = tmp_path / "заявка.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Заявка на раскрой"])
    sheet.append(["Обозначение", "Кол-во", "Толщина", "Марка", "Файл DXF", "Поворот"])
    sheet.append(["А-1", 3, 4, "Ст3сп", "dxf/a1.dxf", "нет"])
    book.save(path)
    order = orders.read_order(path)
    item = order.items[0]
    assert (item.quantity, item.material.thickness, item.material.grade) == (3, 4, "СТ3")
    assert item.dxf == "dxf/a1.dxf" and not item.rotate
    assert order.number == "" and order.products == 1


def test_order_without_headers(tmp_path):
    path = tmp_path / "пусто.xlsx"
    book = openpyxl.Workbook()
    book.active.append(["что-то", "ещё"])
    book.save(path)
    assert orders.read_order(path).problems


# ── the remnants ─────────────────────────────────────────────────────────────


def make_stock(path, rows, whole=(), picture_row: int | None = None):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Дел. остатки"
    sheet.append([])
    sheet.append(["№ п/п", "Толщина\n листа мм", "Габариты мм", "Кол-во", "Эскиз делового\nостатка ",
                  "Цветовая\nмаркеровка", "Фото деловых\nостатков", "Задействованные "])
    for material, size, qty, reserved in rows:
        sheet.append([None, material, size, qty, None, "Синий", None, reserved])
    sheet.append(["Итого:"])
    if whole:
        sheet.append([None, "Целые листа "])
        for material, size, qty in whole:
            sheet.append([None, material, size, qty])
    if picture_row:
        from openpyxl.drawing.image import Image as XlImage
        from PIL import Image

        png = path.with_suffix(".png")
        Image.new("RGB", (20, 20), "white").save(png)
        sheet.add_image(XlImage(str(png)), f"E{picture_row}")
    book.save(path)
    return path


def test_remnants_list(tmp_path):
    path = make_stock(tmp_path / "Деловые остатки.xlsx", [
        ("8 09Г2С", "810х1360", 1, None),
        ("1,5 Ст3", "1250х360", 6, None),
        ("4 Чеч-цаСт3", "560х6000", 7, "762000 - 2шт"),
        ("16  09Г2С", "1500х840", 1, 713158),
        ("??", "абв", 1, None),
    ], whole=[("8 09Г2С", "1500х6000", 2)], picture_row=3)
    s = stock.read_stock(path)
    assert len(s.sheets) == 5
    first = s.sheets[0]
    assert (first.length, first.width, first.row, first.color) == (1360, 810, 3, "Синий")
    assert first.sketch and not s.sheets[1].sketch
    assert s.sheets[1].quantity == 6
    assert s.sheets[2].free_for("762000") and not s.sheets[2].free_for("713158")
    assert not s.sheets[3].free_for("762000")
    whole = s.sheets[-1]
    assert not whole.remnant and whole.quantity == 2 and whole.size == "6000×1500"
    assert any("абв" in p for p in s.problems)


def test_size_of_a_cell():
    assert stock.parse_size("1250х880") == (1250, 880)
    assert stock.parse_size("470 x 2360") == (2360, 470)
    assert stock.parse_size("1500*1570,5") == (1570.5, 1500)
    assert stock.parse_size("нет") is None


# ── DXF ──────────────────────────────────────────────────────────────────────


def save_dxf(path, polygons, circles=()):
    doc = ezdxf.new()
    msp = doc.modelspace()
    for polygon in polygons:
        msp.add_lwpolyline(polygon, close=True)
    for center, r in circles:
        msp.add_circle(center, r)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def rect(w, h, x=0, y=0):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def test_dxf_found_by_designation(tmp_path):
    a8 = save_dxf(tmp_path / "762000ТК" / "8" / "544000.2801121-14 - Ребро большое.dxf", [rect(300, 200)])
    save_dxf(tmp_path / "762000ТК" / "6" / "544000.2801121-14 - Ребро большое.dxf", [rect(300, 200)])
    save_dxf(tmp_path / "762000ТК" / "8" / "_гибы" / "544000.2801122-14.dxf", [rect(10, 10)])
    index = parts.DxfIndex.build([tmp_path])
    assert index.find("544000.2801121-14", 8)[0] == a8
    assert index.find("544000.2801121-14 ", 8)[0] == a8
    assert index.find("544000.2801122-14") == []  # the bend lines are no contour
    assert parts.designation_key("544000.2801121-14 - Ребро") == parts.designation_key("544000-2801121-14")


def test_shape_outer_and_holes(tmp_path):
    path = save_dxf(tmp_path / "a.dxf", [rect(100, 50), rect(20, 10, 10, 10)], [((70, 25), 5)])
    shape = parts.read_shape(path)
    assert len(shape.holes) == 2 and shape.length == pytest.approx(100) and shape.width == pytest.approx(50)
    assert shape.area == pytest.approx(5000 - 200 - math.pi * 25, rel=1e-3)
    assert shape.pierces == 3
    with pytest.raises(parts.ShapeError, match="несколько контуров"):
        parts.read_shape(save_dxf(tmp_path / "b.dxf", [rect(10, 10), rect(10, 10, 50, 0)]))
    with pytest.raises(parts.ShapeError, match="нет замкнутого"):
        parts.read_shape(save_dxf(tmp_path / "c.dxf", []))


def test_resolve_given_path_and_missing(tmp_path):
    save_dxf(tmp_path / "dxf" / "a1.dxf", [rect(10, 10)])
    items = [orders.OrderItem(2, "А-1", "", "", materials.parse("4 Ст3"), 1, 1, dxf="dxf/a1.dxf"),
             orders.OrderItem(3, "Б-2", "", "", materials.parse("4 Ст3"), 1, 1, dxf="dxf/нет.dxf"),
             orders.OrderItem(4, "В-3", "", "", materials.parse("4 Ст3"), 1, 1)]
    found = parts.resolve(items, [tmp_path], base=tmp_path,
                          archive_lookup=lambda d: [tmp_path / "dxf" / "a1.dxf"] if d == "В-3" else [])
    assert found[0].shape is not None
    assert "не найден" in found[1].error
    assert found[2].shape is not None  # from the archive


# ── nesting ──────────────────────────────────────────────────────────────────


def make_part(designation, loops, quantity, material="8 09Г2С", rotate=True):
    item = orders.OrderItem(2, designation, "", material, materials.parse(material), quantity, quantity, rotate=rotate)
    part = parts.Part(item)
    part.shape = parts.shape_from_loops(loops)
    return part


def check_layout(sheet, settings):
    """No overlaps, the gap between details and the margin to the edge, by the true contours."""
    for p in sheet.placements:
        x0, y0, x1, y1 = p.box
        assert x0 >= settings.margin - 1e-6 and y0 >= settings.margin - 1e-6
        assert x1 <= sheet.length - settings.margin + 1e-6 and y1 <= sheet.width - settings.margin + 1e-6
    for a, b in itertools.combinations(sheet.placements, 2):
        assert engine.loops_distance(a.loops, b.loops) >= settings.gap - 1e-6


def test_rectangles_on_a_sheet_keep_gap_and_margin():
    settings = Settings(gap=6, margin=10, passes=1, sheet_sizes=((1000, 500),))
    group = engine.nest_group(materials.parse("8 09Г2С"), [make_part("A", [rect(200, 100)], 12)], [], settings)
    assert not group.unplaced
    total = sum(len(s.placements) for s in group.sheets)
    assert total == 12
    assert len(group.sheets) == 1  # 4 × 3 of 200×100 fit into 980×480 with 6 mm gaps
    check_layout(group.sheets[0], settings)


def test_small_details_go_into_a_hole():
    frame = [rect(400, 400), rect(300, 300, 50, 50)]
    settings = Settings(gap=5, margin=5, passes=1, rotations=(0.0,), sheet_sizes=((420, 420),))
    group = engine.nest_group(materials.parse("8 09Г2С"),
                              [make_part("Рамка", frame, 1), make_part("Пластина", [rect(100, 100)], 4)], [], settings)
    assert len(group.sheets) == 1 and len(group.sheets[0].placements) == 5
    check_layout(group.sheets[0], settings)


def test_rotation_lets_a_long_detail_fit():
    long = make_part("Полка", [rect(100, 900)], 1)
    settings = Settings(gap=5, margin=10, passes=1, sheet_sizes=((1000, 300),))
    group = engine.nest_group(materials.parse("8 09Г2С"), [long], [], settings)
    assert group.sheets and group.sheets[0].placements[0].angle in (90.0, 270.0)
    fixed = make_part("Полка", [rect(100, 900)], 1, rotate=False)
    assert engine.nest_group(materials.parse("8 09Г2С"), [fixed], [], settings).too_big == [fixed]


def test_remnants_first_reserved_skipped_and_new_remnant():
    m = materials.parse("8 09Г2С")
    small = stock.StockSheet(m, 700, 400, quantity=1, row=5)
    kept = stock.StockSheet(m, 2000, 1000, quantity=1, row=6, reserved="713158")
    other = stock.StockSheet(materials.parse("8 10ХСНД"), 2000, 1000, quantity=1, row=7)
    settings = Settings(gap=5, margin=5, passes=1, order="762000", sheet_sizes=((3000, 1500),))
    detail = make_part("A", [rect(300, 150)], 10)
    group = engine.nest_group(m, [detail], [small, kept, other], settings)
    assert group.sheets[0].stock is small
    assert all(s.stock is not kept and s.stock is not other for s in group.sheets)
    assert sum(len(s.placements) for s in group.sheets) == 10
    last = group.sheets[-1]
    assert last.remnant is not None and last.remnant.width == 1500 and last.cut_x < 3000
    for sheet in group.sheets:
        check_layout(sheet, settings)


def test_a_remnant_is_used_once_across_materials():
    m = materials.parse("8 09Г2С")
    remnant = stock.StockSheet(m, 1000, 500, quantity=1, row=3)
    settings = Settings(gap=5, margin=5, passes=1, any_grade=True, sheet_sizes=((1000, 500),))
    a = make_part("A", [rect(900, 400)], 1, "8 09Г2С")
    b = make_part("B", [rect(900, 400)], 1, "8 10ХСНД")
    result = engine.nest([a, b], [remnant], settings)
    used = [s for g in result.groups for s in g.sheets if s.stock is remnant]
    assert len(used) == 1


def test_free_places_by_fft_equal_the_plain_check():
    rng = np.random.default_rng(1)
    grid = rng.random((40, 50)) > 0.85
    mask = rng.random((6, 7)) > 0.5
    places = engine.free_places(grid, mask)
    for i in range(0, 35, 3):
        for j in range(0, 44, 3):
            assert places[i, j] == (not (grid[i:i + 6, j:j + 7] & mask).any())


# ── the result ───────────────────────────────────────────────────────────────


def test_whole_job_writes_the_files(tmp_path):
    folder = tmp_path / "762000ТК" / "8"
    save_dxf(folder / "544000.2801121-14 - Ребро большое.dxf", [[(0, 0), (300, 0), (0, 200)]])
    save_dxf(folder / "544000.2801124-12 - Обечайка.dxf", [rect(600, 400)], [((300, 200), 100)])
    order = make_order(tmp_path / "762000-1шт. - рама.xlsx", [
        ("544000.2801121-14", "Ребро большое", 6, "Лист 8 (09Г2С)"),
        ("544000.2801124-12", "Обечайка (Г в св)", 2, "Лист 8 (09Г2С)"),
        ("999", "Нет DXF", 1, "Лист 8 (09Г2С)"),
    ])
    remnants = make_stock(tmp_path / "Деловые остатки.xlsx", [("8 09Г2С", "810х1360", 1, None)])
    job = prepare(order, remnants)
    assert sum(p.shape is not None for p in job.parts) == 2
    run(job, Settings(passes=1, sheet_sizes=((3000, 1500),)), tmp_path / "out")
    paths = job.paths
    assert paths["report"].is_file() and paths["dxf"] and all(p.is_file() for p in paths["png"])
    assert "762000" in job.text and "999" in job.text
    book = openpyxl.load_workbook(paths["report"])
    assert book.sheetnames[:3] == ["Листы", "Детали", "Остатки"]
    rows = list(book["Листы"].iter_rows(values_only=True))
    assert any(r[2] and str(r[2]).startswith("Остаток 1360×810") for r in rows)
    details = {r[1]: r for r in book["Детали"].iter_rows(min_row=2, values_only=True)}
    assert details["544000.2801121-14"][5] == 6 and details["999"][10] == "DXF не найден"
    doc = ezdxf.readfile(paths["dxf"][0])
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert {output.LAYER_SHEET, output.LAYER_CUT, output.LAYER_TEXT} <= layers
    changes = openpyxl.load_workbook(paths["changes"]).active
    texts = [r for r in changes.iter_rows(values_only=True) if r[0] is not None]
    assert any(r[0] == 3 and r[5] == 1 for r in texts if isinstance(r[0], int))  # row 3 of the list taken once


def test_console(tmp_path, capsys):
    from cad.nesting.__main__ import main

    save_dxf(tmp_path / "A-1.dxf", [rect(100, 100)])
    order = make_order(tmp_path / "1-1шт.xlsx", [("A-1", "Пластина", 3, "Лист 4 (Ст3)")])
    assert main([str(order), "--out", str(tmp_path / "out"), "--лист", "1000x500"]) == 0
    assert "Раскрой 1" in capsys.readouterr().out
    assert (tmp_path / "out" / output.REPORT_NAME).is_file()


# ── the window and the chat ──────────────────────────────────────────────────


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_check_fills_the_table_and_settings(app, tmp_path):
    from PySide6.QtCore import QSettings

    from desktop_browser.nesting_dialog import NestingDialog

    save_dxf(tmp_path / "8" / "A-1 - Пластина.dxf", [rect(100, 50)])
    order = make_order(tmp_path / "500-3шт.xlsx", [("A-1", "Пластина", 2, "Лист 8 (09Г2С)"),
                                                    ("B-2", "Нет файла", 1, "Лист 8 (09Г2С)")])
    remnants = make_stock(tmp_path / "остатки.xlsx", [("8 09Г2С", "810х1360", 1, None)])
    qsettings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    dialog = NestingDialog(None, qsettings, "", str(order))
    assert dialog.products.value() == 3
    dialog.stock.setText(str(remnants))
    dialog.use_stock.setChecked(True)
    assert dialog.check()
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 3).text() == "6"
    assert dialog.table.item(1, 4).text() == "DXF не найден"
    assert "1" in dialog.status.text()
    dialog.gap.setValue(5)
    dialog.rotations.setCurrentIndex(dialog.rotations.findData("none"))
    settings = dialog.settings()
    assert settings.gap == 5 and settings.rotations == (0.0,) and settings.sheet_sizes == ((6000.0, 1500.0),)
    dialog._accept()
    assert qsettings.value("nesting_gap") == "5"
    assert not dialog.archive.isEnabled()  # no archive index


def test_check_without_an_order(app, tmp_path):
    from desktop_browser.nesting_dialog import NestingDialog

    dialog = NestingDialog(None, None, "")
    assert not dialog.check()
    assert dialog.job is None


def test_chat_command(app):
    from desktop_browser.app import nesting_command

    assert nesting_command("/раскрой") == ""
    assert nesting_command('/раскрой "C:\\Заявки\\762000.xlsx"') == "C:\\Заявки\\762000.xlsx"
    assert nesting_command("/nesting a.xlsx") == "a.xlsx"
    assert nesting_command("/раскройка") is None
    assert nesting_command("раскрой") is None


# ── the program of the machine ───────────────────────────────────────────────

# A program of ProNest 13 for HASA Aria MaxCut250: two plates 775×990, kerf 1.7 (the names changed)
PRONEST_HASA = """M63P36
G00X788.189Y9.841
(Seq 1 - A-1 ??????)
F4793
M03
G01X783.189
X7.341
Y1001.539
X784.039
Y13.691
M62P36
Y10.691
Y8.191
M05
M63P36
G00X1576.224Y9.841
(Seq 2 - A-1 ??????)
F4793
M03
G01X1571.224
X795.376
Y1001.539
X1572.074
Y13.691
M62P36
Y10.691
Y8.191
M05
M63P36
M30
"""


def _sheet_of(placements_boxes, part, length=6000, width=1500):
    from cad.nesting import engine as e

    placements = []
    for x0, y0, x1, y1 in placements_boxes:
        loops = [np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], dtype=float)]
        placements.append(e.Placement(part, 0.0, 0.0, 0.0, loops, (x0, y0, x1, y1)))
    return e.NestedSheet(stock.StockSheet(materials.parse("5 Ст3"), length, width), placements)


def test_program_as_pronest_makes_it_for_hasa():
    from cad.nesting import cutting, post

    part = make_part("A-1", [rect(775, 990)], 2, "Лист 5 (3пс) чечевица")
    part.item.name = "Настил"
    sheet = _sheet_of([(8.191, 10.691, 783.189, 1000.689), (796.226, 10.691, 1571.224, 1000.689)], part)
    path = cutting.sheet_path(sheet, cutting.CutSettings(kerf=1.7))
    machine = post.find_machine("hasa-aria-maxcut250")
    text = post.program(path, machine, 4793)
    assert text == PRONEST_HASA.replace("\n", "\r\n")
    assert path.pierces == 2


# ProNest 13, HASA, 12 mm (the 1st and the 4th cut of a sheet; the 4th detail turned by 90°)
PRONEST_HASA_12 = """M63P36
G00X5101.054Y10.976
F2200
M03
G01X5093.054
X6.988
Y193.075
X5094.104
Y15.026
M62P36
Y12.026
Y8.026
M05
M63P36
G00X5292.201Y512.341
F2200
M03
G01Y504.341
Y8.292
X5110.102
Y505.391
X5288.151
M62P36
X5291.151
X5295.151
M05
M63P36
M30
"""


def test_program_as_pronest_makes_it_for_12_mm_and_a_turned_detail():
    from dataclasses import replace

    from cad.nesting import cutting, post
    from cad.nesting import engine as e

    machine = post.find_machine("hasa-aria-maxcut250")
    tech = machine.technology_for(12)
    assert (tech["kerf"], tech["feed"], tech["lead_in"], tech["overcut"], tech["hold"]) == (2.1, 2200, 8, 2.95, 7)
    part = make_part("A-2", [rect(5085.016, 180)], 2, "Лист 12 (09Г2С)")

    def placed(box, angle):
        x0, y0, x1, y1 = box
        loop = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], dtype=float)
        return e.Placement(part, angle, 0.0, 0.0, [loop], box)

    sheet = e.NestedSheet(stock.StockSheet(materials.parse("12 09Г2С"), 6000, 1500),
                          [placed((8.038, 12.026, 5093.054, 192.025), 0.0),
                           placed((5111.152, 9.342, 5291.151, 504.341), 90.0)])
    settings = cutting.CutSettings(kerf=tech["kerf"], lead_in=tech["lead_in"], overcut=tech["overcut"])
    text = post.program(cutting.sheet_path(sheet, settings), replace(machine, thc_off_before_end=tech["hold"]),
                        tech["feed"])
    lines = [line for line in text.split("\r\n") if not line.startswith("(")]
    assert "\n".join(lines) == PRONEST_HASA_12.rstrip("\n") + "\n"


def test_an_axis_a_hair_off_is_written_as_pronest_writes_it():
    from cad.nesting import cutting, post

    path = cutting.SheetPath([cutting.Cut((5297.08, 1034.077), [
        cutting.Move("line", (5289.08, 1034.077)), cutting.Move("line", (5108.03, 1034.0772)),
        cutting.Move("line", (5108.03, 1276.176))])])
    text = post.program(path, post.Machine(id="t", name="t", cut_start=["G00X{x}Y{y}"], cut_end=[], program_end=[]),
                        2200)
    assert text.split("\r\n")[1:4] == ["G01X5289.08", "X5108.03Y1034.077", "Y1276.176"]


def test_numbers_and_technology():
    from cad.nesting import post

    assert [post.number(v) for v in (788.189, 0.0, 3.0, -12.5, -0.0001, 1.0006)] == \
        ["788.189", "0.", "3.", "-12.5", "0.", "1.001"]
    machine = post.find_machine("hasa-aria-maxcut250")
    assert machine.tech(5)[1] == 4793
    feed = machine.tech(7)[1]
    assert machine.tech(6)[1] > feed > machine.tech(8)[1]
    assert machine.tech(1)[1] == machine.tech(3)[1] and machine.tech(100) == machine.tech(30)


def test_hole_arcs_lead_in_from_inside_and_order():
    from cad.nesting import cutting, post

    circle = [(200 + 100 * math.cos(a / 90 * math.pi), 200 + 100 * math.sin(a / 90 * math.pi)) for a in range(180)]
    part = make_part("B-2", [rect(400, 400), circle], 1)
    sheet = engine.NestedSheet(stock.StockSheet(materials.parse("8 09Г2С"), 1000, 500),
                               [engine.Placement(part, 0.0, 10.0, 10.0,
                                                 [np.asarray(loop) + 10 for loop in part.shape.loops],
                                                 (10, 10, 410, 410))])
    path = cutting.sheet_path(sheet, cutting.CutSettings(kerf=2.0))
    assert [c.kind for c in path.cuts] == ["hole", "outer"]  # the hole first
    hole = path.cuts[0]
    assert math.dist(hole.start, (210, 210)) < 100 - 1 - 4  # the lead-in starts inside the hole
    machine = post.Machine(id="t", name="t", thc_off="M62P36", thc_off_before_end=5.5)
    text = post.program(path, machine, 3500)
    arcs = [line for line in text.splitlines() if line.startswith(("G02", "G03"))]
    assert arcs and all("I" in a and "J" in a for a in arcs)
    first = next(line for line in text.splitlines() if line.startswith("G03"))
    assert first.startswith("G03")  # a hole goes counter-clockwise
    assert text.count("M03") == 2 and text.count("M62P36") == 2


def test_bridges_small_holes_and_crop():
    from cad.nesting import cutting

    part = make_part("C-3", [rect(300, 200), rect(4, 4, 50, 50)], 1)
    sheet = _sheet_of([(10, 10, 310, 210)], part, 1000, 500)
    sheet.placements[0].loops.append(np.array([(60, 60), (64, 60), (64, 64), (60, 64)], dtype=float)[::-1])
    sheet.cut_x, sheet.remnant = 320, stock.StockSheet(materials.parse("5 Ст3"), 680, 500)
    path = cutting.sheet_path(sheet, cutting.CutSettings(kerf=1.7, bridges=2, bridge_width=1.5, small_hole=5))
    assert path.skipped_holes == 1
    outer = [c for c in path.cuts if c.kind == "outer"]
    assert len(outer) == 3  # two gaps: three pieces
    assert path.cuts[-1].kind == "crop" and path.cuts[-1].start[0] == pytest.approx(320 - 0.85)
    total = sum(c.length for c in outer)
    plain = cutting.sheet_path(sheet, cutting.CutSettings(kerf=1.7, small_hole=5)).cuts[0].length
    assert total == pytest.approx(plain - 3.0, abs=0.01)


def test_user_machine_profile_replaces_the_shipped_one(tmp_path):
    from cad.nesting import post

    (tmp_path / "hasa-aria-maxcut250.json").write_text(
        '{"id": "hasa-aria-maxcut250", "name": "Мой HASA", "thc_off_before_end": 4}', encoding="utf-8")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    machines, errors = post.load_machines(tmp_path)
    mine = next(m for m in machines if m.id == "hasa-aria-maxcut250")
    assert mine.name == "Мой HASA" and mine.thc_off_before_end == 4
    assert any(m.id == "iso-gcode" for m in machines)
    assert errors and "bad.json" in errors[0]


def test_whole_job_with_programs(tmp_path):
    from cad.nesting import cutting, post

    save_dxf(tmp_path / "A-1.dxf", [rect(300, 200)], [((150, 100), 30)])
    order = make_order(tmp_path / "2-1шт.xlsx", [("A-1", "Пластина", 3, "Лист 8 (09Г2С)")])
    job = prepare(order)
    run(job, Settings(passes=1, sheet_sizes=((1000, 500),)), tmp_path / "out",
        machine=post.find_machine("hasa-aria-maxcut250"), cut=cutting.CutSettings(bridges=1))
    programs = job.paths["programs"]
    assert programs and programs[0].suffix == ".CNC"
    raw = programs[0].read_bytes()
    assert raw.endswith(b"M30\r\n") and raw.isascii()
    assert "F3300" in raw.decode()  # 8 mm from the technology of the profile
    assert job.paths["minutes"] > 0 and "время резки" in job.text
    sheets = openpyxl.load_workbook(job.paths["report"])["Листы"]
    header = [c.value for c in sheets[4]]
    assert "Программа" in header and "Время резки, мин" in header
