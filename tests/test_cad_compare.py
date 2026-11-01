"""«Сравнить версии сборки» (cad/compare.py): snapshots of a report, what changed between
two versions, the workbook — no KOMPAS-3D needed."""
from __future__ import annotations

import pytest

from cad import compare, report
from cad.assembly import ASSEMBLY, DETAIL, OTHER, STANDARD, Component

SHEET10 = "Лист$dБ-ПУ-О-10 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$"


@pytest.fixture(autouse=True)
def _files_in_tmp(tmp_path, monkeypatch):
    from cad import estimate, workload

    monkeypatch.setattr(report, "decisions_path", lambda: tmp_path / "report_decisions.json")
    monkeypatch.setattr(workload, "norms_path", lambda: tmp_path / "time_norms.json")
    monkeypatch.setattr(estimate, "norms_path", lambda: tmp_path / "plant_norms.json")


def det(marking, name, material=SHEET10, kg=2.0, m2=0.2, file=""):
    return Component(DETAIL, marking, name, material, kg * 1000, m2 * 10000, file or f"C:\\m\\{marking or name}.m3d")


def std(name, kg=0.05):
    return Component(STANDARD, "", name, "", kg * 1000, 0, f"C:\\lib\\{name}.m3d")


def asm(marking, name, children):
    return Component(ASSEMBLY, marking, name, "", sum(c.mass_g for c in children), 0, f"C:\\m\\{marking}.a3d",
                     children=children)


def version(old: bool):
    bracket = asm("600.010", "Кронштейн", [
        det("600.011", "Пластина"), det("600.011", "Пластина"),
        *[std("Болт М10-6gx30.58.016 ГОСТ 7798-70") for _ in range(4 if old else 6)],
    ])
    children = [bracket, bracket if old else asm("600.010", "Кронштейн", bracket.children),
                det("600.001", "Рама", "Сталь 3 ГОСТ 380-2005" if old else SHEET10, kg=50.0, m2=3.0)]
    if old:
        children.append(det("600.002", "Косынка", kg=1.0))
    else:
        children.append(det("600.003", "Ребро", kg=1.5))
        children.append(Component(OTHER, "", "Фонарь ФП-1", "", 400, 0, "C:\\lib\\lamp.m3d", section="Прочие изделия"))
    return asm("600.000", "Изделие", children)


def snap(top, cost=None):
    return compare.snapshot(report.build(top), cost)


def test_a_snapshot_holds_the_components_and_the_totals(tmp_path):
    r = report.build(version(old=True))
    path = compare.save_snapshot(r, tmp_path / "600.000 - ведомость и металлоёмкость.xlsx", cost=1000.0)
    assert path.name == "600.000 - ведомость и металлоёмкость - снимок.json"
    data = compare.load_snapshot(path)
    plates = next(i for i in data["items"] if i["marking"] == "600.011")
    assert plates["qty"] == 4 and plates["file"] == "600.011.m3d"
    assert data["totals"]["cost"] == 1000.0 and data["totals"]["fasteners"] == {"Болты М10": 8}
    (tmp_path / "x.json").write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        compare.load_snapshot(tmp_path / "x.json")


def test_added_removed_and_changed():
    diff = compare.compare(snap(version(old=True), 100000.0), snap(version(old=False), 112400.0))
    assert [(c.marking or c.name, c.what) for c in diff.added] == [("600.003", "Добавлено"),
                                                                  ("Фонарь ФП-1", "Добавлено")]
    assert [(c.marking, c.what) for c in diff.removed] == [("600.002", "Удалено")]
    changed = {(c.marking or c.name, c.what): (c.old, c.new) for c in diff.changed}
    assert changed[("600.001", "Материал")][1].startswith("Лист")
    assert changed[("Болт М10-6gx30.58.016 ГОСТ 7798-70", "Количество на изделие")] == ("8", "12")
    assert ("600.011", "Количество на изделие") not in changed  # still 4 plates on the product
    assert [c.name for c in diff.changes if c.bought] == ["Фонарь ФП-1", "Болт М10-6gx30.58.016 ГОСТ 7798-70"]
    assert diff.fasteners() == [("Болты М10", 8, 12)]
    text = compare.summary_text(diff)
    assert ("600.010", "Масса") in changed  # the bracket got two bolts more
    assert "+2 поз., −1 поз., изменено 3" in text and "себестоимость +12 400 ₽" in text


def test_no_changes_and_a_remodelled_mass_within_the_tolerance():
    old = snap(version(old=True))
    new = snap(version(old=True))
    new["items"][0] = dict(new["items"][0], mass_kg=new["items"][0]["mass_kg"] * 1.002)
    diff = compare.compare(old, new)
    assert diff.changes == [] and "изменений нет" in compare.summary_text(diff)


def test_components_without_a_designation_are_matched_by_name_and_file():
    a = {"kind": DETAIL, "marking": "", "name": "Планка", "material": "", "mass_kg": 1.0, "qty": 1, "section": "",
         "file": "a.m3d"}
    assert compare.key(a) != compare.key(dict(a, file="b.m3d"))
    assert compare.key(dict(a, marking="700.001")) == compare.key(dict(a, marking="700.001", file="b.m3d"))


def test_the_workbook(tmp_path):
    from openpyxl import load_workbook

    diff = compare.compare(snap(version(old=True)), snap(version(old=False)))
    path = compare.save_xlsx(diff, tmp_path / "изменения.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames == ["Итоги", "Изменения", "Покупные"]
    totals = {row[0]: row[1:4] for row in wb["Итоги"].iter_rows(min_row=5, values_only=True) if row[0]}
    assert totals["Позиций деталей"] == (3, 3, 0)
    rows = [row for row in wb["Изменения"].iter_rows(min_row=5, values_only=True)]
    assert rows[0][:3] == ("Добавлено", "Деталь", "600.003")
    assert [row[3] for row in wb["Покупные"].iter_rows(min_row=5, values_only=True)] == [
        "Фонарь ФП-1", "Болт М10-6gx30.58.016 ГОСТ 7798-70"]


def test_the_compare_window_needs_two_different_versions(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from desktop_browser.compare_dialog import CompareDialog, version_snapshot
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    old = compare.save_snapshot(report.build(version(old=True)), tmp_path / "old.xlsx")
    new = compare.save_snapshot(report.build(version(old=False)), tmp_path / "new.xlsx")
    dialog = CompareDialog()
    assert not dialog.ok.isEnabled()
    dialog.old.setText(str(old))
    dialog.new.setText(str(old))
    assert not dialog.ok.isEnabled()  # the same file twice
    dialog.new.setText(str(new))
    assert dialog.ok.isEnabled() and dialog.paths() == (str(old), str(new))
    assert version_snapshot(str(old), print)["product"]["marking"] == "600.000"  # a .json needs no KOMPAS
    dialog.deleteLater()


def test_a_notice_of_change_in_the_designation_is_the_same_component():
    """«792720.2801010 Изм2» is 792720.2801010 after the change notice 2: changed, not removed and added."""
    assert compare.base_marking("792720.2801220 Изм2-01") == compare.base_marking("792720.2801220-01")
    old = snap(asm("800.000", "Изделие", [det("800.001", "Рама", kg=50.0, m2=3.0)]))
    new = snap(asm("800.000", "Изделие", [det("800.001 Изм2", "Рама", kg=52.0, m2=3.1)]))
    diff = compare.compare(old, new)
    assert not diff.added and not diff.removed
    assert [(c.what, c.old, c.new) for c in diff.changed] == [("Обозначение", "800.001", "800.001 Изм2"),
                                                              ("Масса", "50 кг", "52 кг")]  # file renamed with it
    assert compare._fmt_mass(6410.52) == "6 410.5 кг" and compare._fmt_mass(2.0041) == "2.004 кг"
