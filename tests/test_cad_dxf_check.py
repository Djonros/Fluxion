"""«Проверка развёрток» (cad/dxf_check.py) on flat patterns drawn here with ezdxf — no KOMPAS-3D needed."""
from __future__ import annotations

from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

from cad import dxf_check  # noqa: E402


def plate(path: Path, width=200.0, height=100.0, holes=(), slots=(), as_lines=True) -> Path:
    """A rectangular plate: the outline as separate lines (as KOMPAS writes it), circle holes, slot rectangles."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    if as_lines:
        for a, b in zip(corners, corners[1:] + corners[:1]):
            msp.add_line(a, b)
    else:
        msp.add_lwpolyline(corners, close=True)
    for x, y, d in holes:
        msp.add_circle((x, y), d / 2)
    for x0, y0, x1, y1 in slots:
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def whats(sheet):
    return sorted((p.what, p.count) for p in sheet.problems)


def test_a_clean_plate(tmp_path):
    sheet = dxf_check.check_file(plate(tmp_path / "a.dxf", holes=[(50, 50, 20), (150, 50, 20)]), 8)
    assert sheet.holes == 2 and sheet.problems == [] and not sheet.error


def test_small_holes_narrow_webs_and_slots(tmp_path):
    path = plate(tmp_path / "b.dxf", holes=[(50, 50, 6), (100, 50, 6), (20, 8, 10), (150, 50, 20), (174, 50, 20)],
                 slots=[(60, 80, 140, 85)])
    sheet = dxf_check.check_file(path, 8)
    assert whats(sheet) == [("Отверстие меньше толщины", 2), ("Узкая перемычка до края", 1),
                            ("Узкая перемычка между отверстиями", 1), ("Узкий вырез", 1)]
    by_what = {p.what: p for p in sheet.problems}
    assert by_what["Узкая перемычка до края"].size == pytest.approx(3.0, abs=0.05)  # ⌀10 at y=8: 8 − 5
    assert by_what["Узкая перемычка между отверстиями"].size == pytest.approx(4.0, abs=0.05)  # 174 − 150 − 20
    assert by_what["Узкий вырез"].size == pytest.approx(5.0)
    assert by_what["Отверстие меньше толщины"].message == "отверстие ⌀6 ×2: 6.0 мм при пороге 8.0 мм"


def test_the_limits_come_from_the_plant_norms(tmp_path):
    path = plate(tmp_path / "c.dxf", holes=[(50, 50, 6)])
    assert whats(dxf_check.check_file(path, 8, {"hole": 0.5})) == []
    assert whats(dxf_check.check_file(path, 5)) == []


def test_a_closed_polyline_outline_and_no_thickness(tmp_path):
    path = plate(tmp_path / "d.dxf", holes=[(50, 50, 4)], as_lines=False)
    sheet = dxf_check.check_file(path, 0)
    assert sheet.holes == 1 and "толщина неизвестна" in sheet.error


def test_the_thickness_of_a_file():
    assert dxf_check.thickness_of(Path("пакет/8/a.dxf")) == 8
    assert dxf_check.thickness_of(Path("пакет/1,5/a.dxf")) == 1.5
    assert dxf_check.thickness_of(Path("x/792720.2801310 Изм2 Лист S10 1шт.dxf")) == 10
    assert dxf_check.thickness_of(Path("x/792720.9711201 лист 12.dxf")) == 12
    assert dxf_check.thickness_of(Path("x/деталь 6мм.dxf")) == 6
    assert dxf_check.thickness_of(Path("x/792720.2801201 - Полка.dxf"), 4) == 4


def test_a_folder_of_a_laser_package_and_the_workbook(tmp_path):
    from openpyxl import load_workbook

    plate(tmp_path / "8" / "a.dxf", holes=[(50, 50, 6)])
    plate(tmp_path / "3" / "b.dxf", holes=[(50, 50, 6)])
    sheets = dxf_check.check_folder(tmp_path)
    assert [(s.file, s.thickness, len(s.problems)) for s in sheets] == [("b.dxf", 3, 0), ("a.dxf", 8, 1)]
    text = dxf_check.summary_text(sheets)
    assert text.startswith("Проверка развёрток: файлов 2, замечаний 1") and "линий гиба в нём нет" in text
    path = dxf_check.save_xlsx(sheets, tmp_path / "Проверка развёрток.xlsx")
    rows = list(load_workbook(path)["Замечания"].iter_rows(min_row=2, values_only=True))
    assert rows == [("a.dxf", 8, "Отверстие меньше толщины", "отверстие ⌀6", 1, 6, 8)]


def test_sheet_limits_are_plant_norms(tmp_path, monkeypatch):
    from cad import estimate

    monkeypatch.setattr(estimate, "norms_path", lambda: tmp_path / "plant_norms.json")
    norms = estimate.load_norms()
    assert norms["sheet_limits"] == dxf_check.RECOMMENDED_LIMITS and estimate.changed(norms) == []
    norms["sheet_limits"]["hole"] = 1.2
    assert "Проверка развёрток" in estimate.changed(norms)
    back = estimate.import_xlsx(estimate.export_xlsx(norms, tmp_path / "n.xlsx"), estimate.recommended())
    assert back["sheet_limits"]["hole"] == 1.2


def test_the_laser_package_checks_its_folder(tmp_path, monkeypatch):
    """At the end of «Пакет на лазер» the window checks the DXF of the package and writes the workbook."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["FLUXION_DISABLE_WEBENGINE"] = "1"
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        from desktop_browser.app import FluxionWindow
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    from cad import estimate

    monkeypatch.setattr(estimate, "norms_path", lambda: tmp_path / "plant_norms.json")
    QApplication.instance() or QApplication([])
    package = tmp_path / "Рама"
    plate(package / "8" / "a.dxf", holes=[(50, 50, 6)])
    window = FluxionWindow(qsettings=QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    text = window._check_flat_patterns(str(package))
    assert text.startswith("Проверка развёрток: файлов 1, замечаний 1") and (package / "Проверка развёрток.xlsx").is_file()
    window.close()
    window.deleteLater()
