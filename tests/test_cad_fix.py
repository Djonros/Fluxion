"""cad/fix.py: the list of fixes and the backups; the window asks before fixing."""
from __future__ import annotations

import os

import pytest

from cad import fix, report
from cad.assembly import ASSEMBLY, DETAIL, Component


@pytest.fixture(autouse=True)
def _decisions_in_tmp(tmp_path, monkeypatch):
    from cad import workload

    monkeypatch.setattr(report, "decisions_path", lambda: tmp_path / "report_decisions.json")
    monkeypatch.setattr(workload, "norms_path", lambda: tmp_path / "time_norms.json")


def det(marking, name, section="", file=""):
    return Component(DETAIL, marking, name, "Сталь 3", 1000, 100, file or f"C:\\m\\{name}.m3d", section=section)


def product():
    lamps = Component(ASSEMBLY, "100.370", "Электрооборудование", "", 0, 0, "C:\\m\\100.000.a3d>Деталь (локальная) 3.m3d",
                      children=[det("А.1", "Фонарь задний"), det("А.1", "Фонарь задний"), det("", "Кабель жгута")])
    unit = Component(ASSEMBLY, "100.200", "Узел", "", 0, 0, "C:\\m\\100.200.a3d",
                     children=[det("К-1", "Клапан", section="Детали"), det("100.201", "Ребро")])
    return Component(ASSEMBLY, "100.000", "Изделие", "", 0, 0, "C:\\m\\100.000.a3d",
                     children=[lamps, unit, det("100.001", "Рама")])


def test_fixes_are_the_bought_units_of_the_report_each_once():
    r = report.build(product())
    fixes = fix.section_fixes(r.top)
    texts = [f.text for f in fixes]
    assert texts == ["А.1 Фонарь задний (в «100.370 Электрооборудование») → раздел «Прочие изделия»",
                     "Кабель жгута (в «100.370 Электрооборудование») → раздел «Прочие изделия»",
                     "К-1 Клапан (в «100.200 Узел») → раздел «Прочие изделия»"]
    # a local subassembly lives in the file of the product; a subassembly with a file changes its own file
    assert fixes[0].file == "" and fixes[2].file == "C:\\m\\100.200.a3d"
    assert fix.changed_files(r.top, fixes) == ["C:\\m\\100.000.a3d", "C:\\m\\100.200.a3d"]


def test_a_unit_marked_as_made_here_is_not_fixed():
    r = report.build(product(), {report.purchase_key("К-1 Клапан"): {"purchased": False}})
    assert [f.component.name for f in fix.section_fixes(r.top)] == ["Фонарь задний", "Кабель жгута"]


def test_backup_is_next_to_the_file_with_the_date(tmp_path):
    model = tmp_path / "Изделие.a3d"
    model.write_bytes(b"model")
    copy = fix.backup(model, "2026-10-23 12-00-00")
    assert copy == tmp_path / "Резервные копии Fluxion" / "Изделие 2026-10-23 12-00-00.a3d"
    assert copy.read_bytes() == b"model"


def test_apply_refuses_a_model_open_in_kompas(monkeypatch, tmp_path):
    import sys
    import types

    path = tmp_path / "1.a3d"
    app = types.SimpleNamespace(Documents=[types.SimpleNamespace(PathName=str(path))])
    module = types.ModuleType("win32com.client")
    module.Dispatch = lambda name: app
    module.CastTo = lambda obj, iface: obj
    monkeypatch.setitem(sys.modules, "win32com.client", module)
    with pytest.raises(fix.AssemblyError, match="открыт в КОМПАС"):
        fix.apply_section_fixes(path, [])


def test_warnings_dialog_offers_the_fix_and_asks_first(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    r = report.build(product())
    dialog = appmod.ReportWarningsDialog(r)
    assert len(dialog.fixes) == 3
    asked = []
    monkeypatch.setattr(appmod.QMessageBox, "question",
                        lambda parent, title, text, *a: asked.append(text) or QMessageBox.No)
    dialog._ask_fix()
    assert asked and "Резервные копии Fluxion" in asked[0] and "C:\\m\\100.200.a3d" in asked[0]
    assert not dialog.fix_requested  # "No": nothing happens
    monkeypatch.setattr(appmod.QMessageBox, "question", lambda *a: QMessageBox.Yes)
    dialog._ask_fix()
    assert dialog.fix_requested


def test_window_fixes_the_model_then_makes_the_report_again(tmp_path, monkeypatch):
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    from cad import assembly

    app = QApplication.instance() or QApplication([])
    reads, applied, backups = [], [], []
    monkeypatch.setattr(assembly, "read", lambda path, with_area=True, log=print: reads.append(path) or product())
    monkeypatch.setattr(report, "default_path", lambda top, folder=None: tmp_path / "r.xlsx")
    monkeypatch.setattr(report, "add_welds", lambda result, log=print: None)
    monkeypatch.setattr(fix, "backup", lambda file, stamp=None: backups.append(file) or tmp_path / "copy.a3d")
    monkeypatch.setattr(fix, "apply_section_fixes",
                        lambda path, fixes, log=print: applied.append(len(fixes)) or ["К-1 Клапан: раздел изменён"])
    monkeypatch.setattr(appmod.QDesktopServices, "openUrl", lambda url: None)
    window = appmod.FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    calls = []

    def mark(result):
        calls.append(result)
        if len(calls) == 1:  # the first time the designer asks for the fix
            window._pending_fixes = fix.section_fixes(result.top)
            return False, {r["id"]: {"checked": False} for r in result.remarks}
        return True, {r["id"]: {"checked": True} for r in result.remarks}

    monkeypatch.setattr(window, "_mark_warnings", mark)
    window.start_assembly_report(str(tmp_path / "100.000.a3d"))
    deadline = time.time() + 10
    while (window.generating or window.report_worker is not None) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert applied == [3] and len(reads) == 2  # fixed, then read again
    assert set(backups) == {r"C:\m\100.000.a3d", r"C:\m\100.200.a3d"}
    assert (tmp_path / "r.xlsx").is_file()
    window.close()


def test_an_assembly_without_a_specification_gets_one():
    import types

    class Obj:
        def __init__(self, section, marking, name):
            self.Section, self.updated = section, False
            self.Columns = types.SimpleNamespace(Column=lambda kind, n, b: types.SimpleNamespace(
                Text=types.SimpleNamespace(Str={4: marking, 5: name}[kind])))

        def SetSection(self, value):  # noqa: N802 — COM API
            self.Section = value

        def Update(self):  # noqa: N802
            self.updated = True

    class Desc:
        def __init__(self):
            # KOMPAS fills a new specification with the objects the components bring
            self.BaseObjects = [Obj(20, "", "Ось 35т")]

        def Update(self):  # noqa: N802
            pass

    class Descs:
        def __init__(self):
            self.items = []

        @property
        def Count(self):  # noqa: N802
            return len(self.items)

        def Add(self, layout, style, name):  # noqa: N802
            assert (layout, style) == (fix.SPC_LAYOUT, fix.SPC_STYLE)
            self.items.append(Desc())
            return self.items[-1]

        def Item(self, i):  # noqa: N802
            return self.items[i]

    descs = Descs()
    doc = types.SimpleNamespace(SpecificationDescriptions=descs)
    axle = Component(DETAIL, "", "Ось 35т", "", 0, 0, r"C:\m\axle.m3d")
    result = fix._section_in(doc, axle, [], lambda obj, iface: obj)
    assert result == "создана спецификация сборки, раздел изменён"
    assert descs.items[0].BaseObjects[0].Section == fix.OTHER_SECTION_NUMBER


# ── material from the «Материалы» library of KOMPAS ──────────────────────


def no_material_product():
    plate = Component(DETAIL, "200.001", "Пластина", "", 1000, 100, r"C:\m\200.001.m3d")
    rib = Component(DETAIL, "200.002", "Ребро", "Сталь 10 ГОСТ 1050-2013", 500, 50, r"C:\lib\200.002.m3d")
    local = Component(DETAIL, "", "Косынка", "", 100, 10, r"C:\m\200.000.a3d>Деталь (локальная) 1.m3d")
    good = Component(DETAIL, "200.003", "Рама", "Сталь 3", 9000, 900, r"C:\m\200.003.m3d")
    unit = Component(ASSEMBLY, "200.100", "Узел", "", 0, 0, r"C:\m\200.100.a3d", children=[plate, rib])
    return Component(ASSEMBLY, "200.000", "Изделие", "", 0, 0, r"C:\m\200.000.a3d",
                     children=[unit, plate, local, good])


def test_material_fixes_each_detail_file_once():
    fixes = fix.material_fixes(no_material_product())
    assert [f.text for f in fixes] == ["200.001 Пластина (в «200.100 Узел»)", "200.002 Ребро (в «200.100 Узел»)",
                                       "Косынка"]
    assert fixes[2].file == ""  # a local detail lives in the file of the product


def test_choose_kompas_material_returns_the_designation_and_density(monkeypatch):
    import sys
    import types

    chosen = types.SimpleNamespace(DisplayName="Лист$d12 ГОСТ 19903-2015;09Г2С ГОСТ 19281-89$", Designation="",
                                   GetProperty=lambda name: types.SimpleNamespace(Value="7850"))
    calls = []
    materials = types.SimpleNamespace(SelectObject=lambda *a: calls.append(a) or chosen)
    module = types.ModuleType("win32com.client")
    module.Dispatch = lambda name: types.SimpleNamespace(Materials=materials)
    monkeypatch.setitem(sys.modules, "win32com.client", module)
    assert fix.choose_kompas_material(5) == ("Лист$d12 ГОСТ 19903-2015;09Г2С ГОСТ 19281-89$", 7.85)
    assert calls == [("", 7, 5, 0)]  # material, sortament and its instance may be chosen
    materials.SelectObject = lambda *a: None
    assert fix.choose_kompas_material() is None  # cancelled


def test_material_dialog_warns_about_files_outside_the_product(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    fixes = fix.material_fixes(no_material_product())
    dialog = appmod.MaterialFixDialog(fixes, r"C:\m\200.000.a3d")
    assert not dialog.apply_button.isEnabled()  # no material yet
    dialog.set_material("Лист$d12 ГОСТ 19903-2015;09Г2С ГОСТ 19281-89$", 7.85)
    assert dialog.apply_button.isEnabled()
    dialog.list.item(2).setCheckState(Qt.Unchecked)
    text = dialog.confirm_text()
    assert "Лист 12 ГОСТ 19903-2015" in text and "Косынка" not in text
    assert "вне папки изделия" in text and r"C:\lib\200.002.m3d" in text
    chosen = dialog.chosen()
    assert [f.component.name for f in chosen] == ["Пластина", "Ребро"] and chosen[0].density == 7.85
