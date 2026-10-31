"""Summary statement and metal report of an assembly (cad/assembly.py, cad/report.py)
on made-up trees and a fake KOMPAS — no KOMPAS-3D needed."""
from __future__ import annotations

import sys
import types

import pytest

from cad import assembly, report
from cad.assembly import ASSEMBLY, DETAIL, OTHER, STANDARD, Component

SHEET10 = "Лист$dБ-ПУ-О-10 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$"


@pytest.fixture(autouse=True)
def _decisions_in_tmp(tmp_path, monkeypatch):
    from cad import workload

    monkeypatch.setattr(report, "decisions_path", lambda: tmp_path / "report_decisions.json")
    monkeypatch.setattr(workload, "norms_path", lambda: tmp_path / "time_norms.json")


def det(marking, name, material="Сталь 20 ГОСТ 1050-2013", kg=1.0, m2=0.1, file=""):
    return Component(DETAIL, marking, name, material, kg * 1000, m2 * 10000, file or f"C:\\m\\{marking or name}.m3d")


def std(name, kg=0.05):
    return Component(STANDARD, "", name, "", kg * 1000, 0, f"C:\\lib\\{name}.m3d")


def asm(marking, name, children, kg=None, file=""):
    mass = kg if kg is not None else sum(c.mass_kg for c in children)
    return Component(ASSEMBLY, marking, name, "", mass * 1000, 0, file or f"C:\\m\\{marking}.a3d", children=children)


def product():
    """Product: 2 brackets (each: 2 plates + 4 bolts), a frame plate, a welded unit whose mass
    is set by hand, a bought axle and two tyres («Прочие изделия»)."""
    bracket = lambda: asm("100.010", "Кронштейн", [  # noqa: E731
        det("100.011", "Пластина", SHEET10, kg=2.0, m2=0.2), det("100.011", "Пластина", SHEET10, kg=2.0, m2=0.2),
        *[std("Болт М10х30 ГОСТ 7798-70") for _ in range(4)],
    ])
    axle = Component(OTHER, "", "Ось 35т", "", 300000, 0, "C:\\m\\axle.a3d", section="Прочие изделия")
    unit = asm("100.020", "Узел сварной", [det("", "NONE", kg=0.0, m2=0.0)], kg=40.0)
    tyre = lambda n: Component(  # noqa: E731
        OTHER, "", "Шина", "Смесь резиновая 3063", 61000, 0,
        f"C:\\m\\100.000.a3d>100.000>Деталь (локальная) {n}.m3d", section="Прочие изделия")
    return asm("100.000", "Изделие", [
        bracket(), bracket(),
        det("100.001", "Рама", "Сталь 3 ГОСТ 380-2005", kg=50.0, m2=3.0),
        axle, unit, tyre(1), tyre(2),
    ])


def test_default_material_of_kompas_counts_as_none():
    assert assembly.no_material("Сталь 10  ГОСТ 1050-2013")  # what KOMPAS gives a new part
    assert assembly.no_material("Сталь 10")
    assert not assembly.no_material("Лист$d10 ГОСТ 19903-2015;Сталь 10 ГОСТ 1050-2013$")  # a sheet chosen on purpose
    assert not assembly.no_material("Сталь 10ХСНД ГОСТ 19281-2014")
    assert not assembly.no_material("Сталь 20 ГОСТ 1050-2013")


def test_material_text():
    assert assembly.material_parts(SHEET10) == ("Лист Б-ПУ-О-10 ГОСТ 19903-2015", "345-09Г2С ГОСТ 17066-94")
    assert assembly.material_text(SHEET10) == "Лист Б-ПУ-О-10 ГОСТ 19903-2015 / 345-09Г2С ГОСТ 17066-94"
    assert assembly.material_text("Сталь 10  ГОСТ 1050-2013") == "Сталь 10 ГОСТ 1050-2013"
    assert assembly.no_material("") and assembly.no_material("Без указания материала")
    assert not assembly.no_material("Сталь 20")


def test_quantities_multiply_down_the_tree():
    r = report.build(product())
    titles = [s.assembly.title for s in r.sections]
    assert titles == ["100.000 Изделие", "100.010 Кронштейн", "100.020 Узел сварной"]  # a bought axle is not opened
    bracket = r.sections[1]
    assert bracket.qty_product == 2
    plate = next(l for l in bracket.lines if l.component.name == "Пластина")
    assert (plate.qty, plate.qty_product) == (2, 4)
    details = {l.component.title: l.qty_product for l in r.details}
    assert details["100.011 Пластина"] == 4 and "Шина" not in details
    assert {l.component.name: l.qty_product for l in r.other} == {"Ось 35т": 1, "Шина": 2}  # local parts: one line
    assert {l.component.name: l.qty_product for l in r.standard} == {"Болт М10х30 ГОСТ 7798-70": 8}


def test_metal_by_material_and_mass_check():
    r = report.build(product())
    metal = {row["material"]: row for row in r.metal}
    sheet = metal["Лист Б-ПУ-О-10 ГОСТ 19903-2015 / 345-09Г2С ГОСТ 17066-94"]
    assert (sheet["items"], sheet["qty"], sheet["mass_kg"]) == (1, 4, 8.0)
    assert sheet["area_m2"] == pytest.approx(0.8)
    assert metal["Сталь 3 ГОСТ 380-2005"]["mass_kg"] == 50.0
    assert "Смесь резиновая 3063" not in metal  # bought tyres are not metal of the product
    assert sum(row["share"] for row in r.metal) == pytest.approx(1.0)
    # the welded unit: its mass is set on the assembly, not in its parts
    assert [(s.assembly.name, own) for s, own in r.assigned] == [("Узел сварной", 40.0)]
    assert r.other_mass_kg == 422.0
    total = r.details_mass_kg + r.standard_mass_kg + r.other_mass_kg + r.assigned_mass_kg
    assert total == pytest.approx(r.top.mass_kg)
    assert "Сверка массы" in report.summary_text(r)


def test_tree_in_specification_order():
    top = product()
    bracket = top.children[0]
    bracket.children[0].position = bracket.children[1].position = "2"
    for bolt in bracket.children[2:]:
        bolt.position = "5"
    top.children[2].position = "3"  # Рама
    rows = report.tree(top)
    shown = [(r.number, r.depth, r.component.title, r.qty, r.qty_product) for r in rows]
    assert shown[0] == ("1", 0, "100.000 Изделие", 1, 1)
    # sections in the order of a specification: assemblies, details, standard, other
    assert [r.section for r in rows if r.depth == 1] == [
        "Сборочные единицы", "Сборочные единицы", "Детали", "Прочие изделия", "Прочие изделия"]
    assert ("1.1", 1, "100.010 Кронштейн", 2, 2) in shown
    assert ("1.1.1", 2, "100.011 Пластина", 2, 4) in shown  # nested under its assembly
    assert ("1.1.2", 2, "Болт М10х30 ГОСТ 7798-70", 4, 8) in shown
    assert ("1.2", 1, "100.020 Узел сварной", 1, 1) in shown
    assert ("1.3", 1, "100.001 Рама", 1, 1) in shown
    assert [r.component.name for r in rows if r.depth == 1][-2:] == ["Ось 35т", "Шина"]


def test_bought_units_are_found_by_name_and_reported():
    top = product()
    deck = det("", "Настил решётчатый 30мм", "Сталь 10 ГОСТ 1050-2013", kg=40.0, m2=5.0)
    deck.section = "Детали"
    top.children.append(deck)
    r = report.build(top)
    assert "Настил решётчатый 30мм" in [l.component.name for l in r.other]
    assert "Настил решётчатый 30мм" not in [l.component.name for l in r.details]
    first = r.remarks[0]
    assert first["level"] == report.ATTENTION and first["problem"] == "Покупное не в «Прочих изделиях»"
    assert "Отнесите его к «Прочим изделиям»" in first["message"]
    text = report.warnings_text(r)
    assert text.startswith("Перед оформлением проверьте") and "Настил решётчатый" in text


def test_default_material_warns_the_designer():
    top = product()
    top.children.append(det("100.003", "Косынка", "Сталь 10  ГОСТ 1050-2013"))
    r = report.build(top)
    warning = next(x for x in r.remarks if x["title"] == "100.003 Косынка")
    assert warning["problem"] == "Не задан материал" and "Сталь 10" in warning["message"]
    assert "отображаться неправильно" in warning["message"]


def test_marks_are_kept_per_product_and_change_the_report():
    top = product()
    deck = det("", "Настил решетчатый 30мм", kg=40.0)
    top.children.append(deck)
    r = report.build(top)
    assert r.unchecked == r.remarks  # nothing marked yet: the document is blocked
    rid = next(x["id"] for x in r.remarks if x["title"] == "Настил решетчатый 30мм")
    assert rid.endswith("|100.000 Изделие")  # a place is a component in an assembly
    marks = {x["id"]: {"checked": True} for x in r.remarks}
    marks[rid] = {"checked": True, "purchased": False}  # "it is made here"
    assert report.purchase_changed(r, marks)
    kept = report.decisions_from_marks(r, marks)
    assert kept[report.purchase_key("Настил решетчатый 30мм")] == {"purchased": False}
    report.save_decisions(r"C:\m\100.000.a3d", kept)
    assert report.load_decisions(r"c:\M\100.000.A3D") == kept  # the same file, any case

    again = report.build(product_with(deck_copy()), kept)
    assert "Настил решетчатый 30мм" in [l.component.name for l in again.details]  # counted as a detail now
    made_here = next(x for x in again.remarks if x["id"] == rid)
    assert made_here["purchased"] is False and made_here["checked"]
    assert not again.unchecked or all(x["id"] not in marks for x in again.unchecked)


def deck_copy():
    return det("", "Настил решетчатый 30мм", kg=40.0)


def product_with(extra):
    top = product()
    top.children.append(extra)
    return top


def test_warnings_dialog_is_blocked_until_every_place_is_marked(tmp_path):
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
    r = report.build(product_with(deck_copy()))
    dialog = ReportWarningsDialog(r)
    assert not dialog.make_button.isEnabled()
    rows = dialog.table.rowCount()
    for row in range(rows - 1):
        dialog.table.item(row, 0).setCheckState(Qt.Checked)
    assert not dialog.make_button.isEnabled() and dialog.counter.text() == f"Отмечено {rows - 1} из {rows}"
    dialog.table.item(rows - 1, 0).setCheckState(Qt.Checked)
    assert dialog.make_button.isEnabled()
    deck_row = next(i for i, x in enumerate(r.remarks) if x["purchased"] is not None)
    dialog.table.item(deck_row, 1).setCheckState(Qt.Unchecked)
    assert dialog.marks()[r.remarks[deck_row]["id"]] == {"checked": True, "purchased": False}
    # a double click opens the model of the row
    import desktop_browser.app as appmod

    opened = []
    real = appmod.QDesktopServices.openUrl
    appmod.QDesktopServices.openUrl = lambda url: opened.append(url.toLocalFile())
    try:
        dialog.table.cellDoubleClicked.emit(deck_row, 4)
    finally:
        appmod.QDesktopServices.openUrl = real
    assert [local(p) for p in opened] == [local(report.model_file(r.remarks[deck_row]["file"]))]
    assert dialog.table.item(deck_row, 4).toolTip().startswith("Открыть:")


def test_remarks():
    r = report.build(product())
    problems = {(x["problem"], x["title"]) for x in r.remarks}
    assert not any(title in ("Ось 35т", "Шина") for _, title in problems)  # bought: no remarks
    assert ("Деталь без обозначения", "NONE") in problems
    assert ("Нулевая масса", "NONE") in problems
    assert ("Деталь без обозначения", "100.001 Рама") not in problems
    where = {x["title"]: x["where"] for x in r.remarks}
    assert where["NONE"] == "100.020 Узел сварной"


def test_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    r = report.build(product())
    path = report.save_xlsx(r, tmp_path / "r.xlsx", source="100.000.a3d")
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Предупреждения", "Сводная ведомость", "Детали", "Металлоёмкость", "Прочие изделия",
                             "Покраска", "Трудоёмкость", "Технологичность"]
    assert wb.active.title == "Предупреждения"  # the designer sees it first
    tree_rows = list(wb["Сводная ведомость"].iter_rows(min_row=5, values_only=True))
    assert all(not row[5] for row in tree_rows if row[4] == "Сборочные единицы")  # no material on assemblies
    others = [row[:3] for row in wb["Прочие изделия"].iter_rows(min_row=5, values_only=True)]
    assert (None, "Шина", 2) in others
    ws = wb["Сводная ведомость"]
    assert ws["A4"].value == "№" and ws["C5"].value == "100.000"
    assert ws.row_dimensions[6].outline_level == 1  # subassemblies fold in Excel
    rows = list(wb["Металлоёмкость"].iter_rows(values_only=True))
    assert any(row[0] == "Изделие по КОМПАС-3D" for row in rows)
    assert any(row[0] and row[0].startswith("Масса, заданная у сборочных единиц") for row in rows)
    # every object is a link to its model: a local part opens its assembly
    assert ws["C5"].hyperlink.target == r"C:\m\100.000.a3d"
    details = {row[0].value: row[0].hyperlink for row in wb["Детали"].iter_rows(min_row=5) if row[0].value}
    assert details["100.001"].target == r"C:\m\100.001.m3d"
    tyre = next(row for row in wb["Прочие изделия"].iter_rows(min_row=5) if row[1].value == "Шина")
    assert tyre[1].hyperlink.target == r"C:\m\100.000.a3d"
    warn = next(row for row in wb["Предупреждения"].iter_rows(min_row=5) if row[2].value == "NONE")
    assert warn[2].hyperlink.target == r"C:\m\NONE.m3d"


def test_links_for_the_chat():
    r = report.build(product())
    links = report.report_links(r, r"C:\out\r.xlsx")
    assert links[0] == {"file": r"C:\out\r.xlsx", "label": "Отчёт: r.xlsx"}
    assert links[1]["file"] == r"C:\m\100.000.a3d" and links[1]["label"].startswith("Изделие: 100.000")
    files = [l["file"] for l in links]
    assert r"C:\m\NONE.m3d" in files and len(files) == len(set(files))  # each model once
    assert report.model_file(r"C:\m\a.a3d>a>Деталь (локальная) 1.m3d") == r"C:\m\a.a3d"


def test_default_path_never_overwrites(tmp_path):
    top = product()
    first = report.default_path(top, tmp_path)
    assert first.name == "100.000 - ведомость и металлоёмкость.xlsx"
    first.write_bytes(b"x")
    assert report.default_path(top, tmp_path).name == "100.000 - ведомость и металлоёмкость (2).xlsx"


# ── reading through a fake KOMPAS ────────────────────────────────────────


class FakePart:
    def __init__(self, marking, name, detail=True, mass=1000.0, parts=(), standard=False, area=100.0):
        self.Marking, self.Name, self.Detail, self.Mass = marking, name, detail, mass
        self.Standard, self.Material, self.Area = standard, "Сталь 20", area
        self.FileName = f"C:\\m\\{marking or name}.m3d"
        self.Parts = list(parts)


class FakeDoc:
    def __init__(self, path, top):
        self.PathName, self.TopPart, self.closed = path, top, None

    def Close(self, mode):  # noqa: N802 — COM API
        self.closed = mode


class FakeDocuments(list):
    def __init__(self, existing, top):
        super().__init__(existing)
        self.top, self.opened = top, []

    def Open(self, path, visible, read_only):  # noqa: N802 — COM API
        doc = FakeDoc(path, self.top)
        self.opened.append((path, visible, read_only, doc))
        return doc


@pytest.fixture
def fake_kompas(monkeypatch, tmp_path):
    top = FakePart("1.000", "Изделие", detail=False, mass=3000.0, parts=[
        FakePart("1.001", "Пластина"), FakePart("1.001", "Пластина"),
        FakePart("", "Болт", standard=True, mass=50.0),
        FakePart("1.100", "Узел", detail=False, mass=1000.0, parts=[FakePart("1.101", "Ребро")]),
    ])
    app = types.SimpleNamespace(HideMessage=0, Documents=FakeDocuments([], top))
    module = types.ModuleType("win32com.client")
    module.Dispatch = lambda name: app
    module.CastTo = lambda obj, interface: obj
    monkeypatch.setitem(sys.modules, "win32com.client", module)
    path = tmp_path / "1.000.a3d"
    path.write_bytes(b"")
    return app, path


def test_read_opens_read_only_and_closes_unsaved(fake_kompas):
    app, path = fake_kompas
    top = assembly.read(path, log=lambda line: None)
    (opened_path, visible, read_only, doc), = app.Documents.opened
    assert (visible, read_only) == (False, True)
    assert doc.closed == 0  # kdDoNotSaveChanges
    assert app.HideMessage == 0  # the user's setting is put back
    assert [c.title for c in top.children] == ["1.001 Пластина", "1.001 Пластина", "Болт", "1.100 Узел"]
    assert top.children[3].children[0].title == "1.101 Ребро"
    assert top.children[0].area_cm2 == 100.0 and top.children[2].kind == STANDARD


def test_read_never_closes_a_document_the_designer_has_open(fake_kompas):
    app, path = fake_kompas
    mine = FakeDoc(str(path), app.Documents.top)
    app.Documents.append(mine)
    assembly.read(path, log=lambda line: None)
    assert not app.Documents.opened and mine.closed is None


class FakeSpecObject:
    def __init__(self, section, marking, name):
        self.Section = section
        self.Columns = types.SimpleNamespace(Column=lambda kind, number, block: types.SimpleNamespace(
            Text=types.SimpleNamespace(Str={4: marking, 5: name}[kind])))


def fake_spec(*objects):
    desc = types.SimpleNamespace(BaseObjects=[FakeSpecObject(*o) for o in objects])
    return types.SimpleNamespace(Count=1, Item=lambda i: desc)


def test_read_takes_the_section_from_the_specification_of_the_assembly(fake_kompas):
    """The property of the component is empty, its specification object is in «Прочие
    изделия» (30): it is bought, and a subassembly is read with its own specification."""
    app, path = fake_kompas
    app.Documents.top.Parts[3].Parts.append(FakePart("Р-1", "Клапан"))
    app.Documents.Count = 1
    sub_doc = FakeDoc("", None)
    sub_doc.SpecificationDescriptions = fake_spec((30, "Р-1", "Клапан"), (20, "1.101", "Ребро"))
    unit = app.Documents.top.Parts[3]
    unit.GetOpenDocumentParam = lambda: types.SimpleNamespace(Visible=True, ReadOnly=False)

    def open_source(param):
        assert (param.Visible, param.ReadOnly) == (False, True)
        app.Documents.Count = 2
        return sub_doc

    unit.OpenSourceDocument = open_source
    real_open = app.Documents.Open

    def open_top(path_, visible, read_only):
        doc = real_open(path_, visible, read_only)
        doc.SpecificationDescriptions = fake_spec((20, "1.001", "Пластина"), (15, "1.100", "Узел"))
        return doc

    app.Documents.Open = open_top
    top = assembly.read(path, log=lambda line: None)
    unit_c = top.children[3]
    assert top.children[0].section == "Детали" and unit_c.section == "Сборочные единицы"
    kinds = {c.name: (c.kind, c.section) for c in unit_c.children}
    assert kinds["Клапан"] == (OTHER, "Прочие изделия") and kinds["Ребро"][1] == "Детали"
    assert sub_doc.closed == 0  # the source of the subassembly is closed unsaved


def test_spec_sections_by_name_when_the_designation_differs():
    s = assembly.SpecSections({("A-1", "Кран"): "Прочие изделия", ("", "Болт"): "Стандартные изделия",
                               ("1", "Ось"): "Детали", ("2", "Ось"): "Сборочные единицы"})
    assert s.get("A-1 ", "Кран") == "Прочие изделия"
    assert s.get("A-1 Изм.1", "Кран") == "Прочие изделия"  # by the name, it is unique
    assert s.get("3", "Ось") == ""  # the name is ambiguous
    assert s.get("A-1-01", "Кран") == ""  # another execution: its own specification object or none


def test_read_errors(tmp_path):
    with pytest.raises(assembly.AssemblyError, match="не найден"):
        assembly.read(tmp_path / "nope.a3d")
    part = tmp_path / "p.m3d"
    part.write_bytes(b"")
    with pytest.raises(assembly.AssemblyError, match=".a3d"):
        assembly.read(part)


# ── the window: Инструменты → Ведомость и металлоёмкость сборки ─────────


def test_window_report(tmp_path, monkeypatch):
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    app = QApplication.instance() or QApplication([])
    opened = []
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)  # Pro
    monkeypatch.setattr(assembly, "read", lambda path, with_area=True, log=print: product())
    monkeypatch.setattr(report, "default_path", lambda top, folder=None: tmp_path / "r.xlsx")
    monkeypatch.setattr(report, "add_welds", lambda result, log=print: None)  # no KOMPAS in tests
    monkeypatch.setattr(appmod.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    window = appmod.FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    asked = []

    def mark_all(result):
        asked.append(result)
        return True, {r["id"]: {"checked": True, **({"purchased": r["purchased"]} if r["purchased"] is not None else {})}
                      for r in result.remarks}

    monkeypatch.setattr(window, "_mark_warnings", mark_all)
    window.start_assembly_report(str(tmp_path / "100.000.a3d"))
    deadline = time.time() + 10
    while (window.generating or window.report_worker is not None) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    text = window.messages.toPlainText()
    assert "100.000 Изделие" in text and "Сверка массы" in text
    assert "Ссылки:" in text and "Изделие: 100.000 Изделие" in text  # links to the report and the models
    assert "file:///" in window.messages.toHtml()
    assert (tmp_path / "r.xlsx").is_file() and opened == [str(tmp_path / "r.xlsx").replace("\\", "/")]
    assert asked and asked[0].remarks  # the warnings came first, every one marked
    marks = report.load_decisions(asked[0].top.file)
    assert marks and all(m["checked"] for m in marks.values())  # kept for the next report
    window.close()


def test_window_report_cancelled_at_the_warnings(tmp_path, monkeypatch):
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    app = QApplication.instance() or QApplication([])
    import licensing

    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)  # Pro
    monkeypatch.setattr(assembly, "read", lambda path, with_area=True, log=print: product())
    monkeypatch.setattr(report, "default_path", lambda top, folder=None: tmp_path / "r.xlsx")
    monkeypatch.setattr(report, "add_welds", lambda result, log=print: None)
    window = appmod.FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    monkeypatch.setattr(window, "_mark_warnings", lambda result: (False, {r["id"]: {"checked": False}
                                                                         for r in result.remarks}))
    window.start_assembly_report(str(tmp_path / "100.000.a3d"))
    deadline = time.time() + 10
    while (window.generating or window.report_worker is not None) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not (tmp_path / "r.xlsx").exists()
    assert "Отчёт не сформирован" in window.messages.toPlainText()
    window.close()


def local(path: str) -> str:
    """A Windows path as QUrl.toLocalFile gives it back: forward slashes (on Linux Qt
    also puts "/" before the drive)."""
    return path.replace("\\", "/").lstrip("/")


def borrowed_product():
    """A product in «…/Архив/792720/Рабочка/Модель» with its own parts, a valve from another
    project, a lamp from the shared «Покупные» and a subassembly borrowed from another project."""
    root = r"C:\Архив\792720\Рабочка\Модель"
    valve = Component(OTHER, "35160200010", "Кран быстрого растормаживания", "", 900, 0,
                      r"C:\КБ\Сибирь Трейлер 2016-2020\733322\Кран.m3d", section="Прочие изделия")
    lamp = Component(OTHER, "А.3731.007", "Фонарь габаритный", "", 200, 0,
                     r"C:\Диск\Изделия\Покупные\Атланта\Фонарь.m3d", section="Прочие изделия")
    borrowed = asm("723095.2720022", "Фиксатор", [det("723095.2720023", "Пластина", file=r"C:\Архив\723095\П.m3d")],
                   file=r"C:\Архив\723095\Фиксатор.a3d")
    pneumatics = asm("792720.3500001", "Установка пневматики", [valve, lamp, valve], file=root + r"\Пневматика.a3d")
    own = det("792720.2801001", "Лонжерон", file=r"C:\Архив\792720\Изм2\Лонжерон.m3d")
    bolt = Component(STANDARD, "", "Болт М10", "", 50, 0,
                     r"C:\Program Files\ASCON\KOMPAS-3D v24\Libs\PolynomLib\Болт.m3d")
    return asm("792720.0000010", "Трейлер", [pneumatics, borrowed, own, bolt],
               file=root + r"\792720.0000010 - Терминальный трейлер.a3d")


def test_models_from_outside_the_product_are_warned_about():
    top = borrowed_product()
    assert report.product_folder(top) == r"C:\Архив\792720"
    r = report.build(top)
    out = {x["title"]: x for x in r.remarks if x["problem"] in (report.OTHER_PROJECT, report.SHARED_LIBRARY)}
    assert set(out) == {"35160200010 Кран быстрого растормаживания", "А.3731.007 Фонарь габаритный",
                        "723095.2720022 Фиксатор"}  # not the insides of the borrowed one, not a KOMPAS bolt
    valve = out["35160200010 Кран быстрого растормаживания"]
    assert valve["problem"] == report.OTHER_PROJECT and valve["qty"] == 2
    assert valve["where"] == "792720.3500001 Установка пневматики"
    assert r"C:\КБ\Сибирь Трейлер 2016-2020\733322\Кран.m3d" in valve["message"]
    assert out["А.3731.007 Фонарь габаритный"]["problem"] == report.SHARED_LIBRARY
    # marked once — remembered for the product
    report.save_decisions(top.file, {valve["id"]: {"checked": True}})
    again = report.build(borrowed_product(), report.load_decisions(top.file))
    assert next(x for x in again.remarks if x["id"] == valve["id"])["checked"]


def test_borrowed_model_path_shows_the_file_in_its_folder(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    r = report.build(borrowed_product())
    dialog = appmod.ReportWarningsDialog(r)
    row = next(i for i, x in enumerate(r.remarks) if x["problem"] == report.OTHER_PROJECT)
    assert dialog.table.item(row, 6).toolTip().startswith("Показать в папке: ")
    opened = []
    monkeypatch.setattr(appmod.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    dialog.open_model(row, 6)  # the file does not exist here: its folder opens
    dialog.open_model(row, 4)  # the title: the model itself
    file = report.model_file(r.remarks[row]["file"])
    import ntpath

    assert [local(p).lower() for p in opened] == [local(ntpath.dirname(file)).lower(), local(file).lower()]


def test_report_and_its_norm_control_are_pro(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    import licensing

    assert report.PRO_FEATURE in licensing.PRO_FEATURES
    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    read = []
    monkeypatch.setattr(assembly, "read", lambda *a, **k: read.append(a) or product())
    from cad.__main__ import main

    assert main(["--report", str(tmp_path / "100.000.a3d")]) == 2 and not read
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    told = []
    monkeypatch.setattr(appmod.QMessageBox, "information", lambda parent, title, text: told.append(text))
    window = appmod.FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    window.start_assembly_report(str(tmp_path / "100.000.a3d"))
    assert not window.generating and getattr(window, "report_worker", None) is None and not read
    assert told and "функция Pro" in told[0] and "Лицензия" in told[0]
    window.close()
