"""«Заявка на закупку» (cad/purchase.py): several products in their quantities, sheets by
thickness with the count of sheets, rolled metal, fasteners, bought units — no KOMPAS-3D needed."""
from __future__ import annotations

import math

import pytest

from cad import compare, estimate, purchase, report
from cad.assembly import ASSEMBLY, DETAIL, OTHER, STANDARD, Component

SHEET10 = "Лист$dБ-ПУ-О-10 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$"
SHEET3 = "Лист$dБ-ПН-О-3 ГОСТ 19903-2015;Ст3сп ГОСТ 14637-89$"
TUBE = "Труба 120х120х8-ГОСТ 30245-2012 / Ст3сп"


@pytest.fixture(autouse=True)
def _files_in_tmp(tmp_path, monkeypatch):
    from cad import workload

    monkeypatch.setattr(report, "decisions_path", lambda: tmp_path / "report_decisions.json")
    monkeypatch.setattr(workload, "norms_path", lambda: tmp_path / "time_norms.json")
    monkeypatch.setattr(estimate, "norms_path", lambda: tmp_path / "plant_norms.json")


def det(marking, name, material, kg, **extra):
    return Component(DETAIL, marking, name, material, kg * 1000, 1000, f"C:\\m\\{marking}.m3d", **extra)


def trailer():
    return Component(ASSEMBLY, "900.000", "Трейлер", "", 0, 0, "C:\\m\\900.000.a3d", children=[
        det("900.001", "Стенка", SHEET10, 78.5),  # 1 m² of 10 mm steel
        det("900.002", "Косынка", SHEET3, 2.355),  # 0.1 m² of 3 mm
        det("900.002", "Косынка", SHEET3, 2.355),
        det("900.003", "Лонжерон", TUBE, 100.0),
        Component(STANDARD, "", "Болт М12-6gx40.58.016 ГОСТ 7798-70", "", 50, 0, "C:\\lib\\b.m3d"),
        Component(OTHER, "", "Ось 35т", "", 300000, 0, "C:\\m\\axle.a3d", section="Прочие изделия"),
        Component(OTHER, "", "Ось 35т", "", 300000, 0, "C:\\m\\axle.a3d", section="Прочие изделия"),
    ])


def cart():
    return Component(ASSEMBLY, "910.000", "Тележка", "", 0, 0, "C:\\m\\910.000.a3d", children=[
        det("910.001", "Площадка", SHEET10, 39.25),  # 0.5 m²
        Component(STANDARD, "", "Болт М12-6gx40.58.016 ГОСТ 7798-70", "", 50, 0, "C:\\lib\\b.m3d"),
    ])


def snap(top):
    return compare.snapshot(report.build(top))


def test_a_snapshot_knows_the_sheet_thickness():
    items = {i["marking"]: i for i in snap(trailer())["items"]}
    assert items["900.001"]["sheet_t"] == 10 and items["900.002"]["sheet_t"] == 3
    assert items["900.003"]["sheet_t"] == 0  # a tube


def test_the_order_of_several_products():
    norms = estimate.recommended()
    norms["items"] = [{"match": "Ось 35т", "price": 45000}]
    order = purchase.build([(snap(trailer()), 3), (snap(cart()), 5)], norms)
    assert order.products == [("900.000 Трейлер", 3), ("910.000 Тележка", 5)]
    sheets = {r.thickness: r for r in order.sheets}
    ten = sheets[10]
    assert ten.area_m2 == pytest.approx(3 * 1.0 + 5 * 0.5) and ten.pieces == 8
    assert ten.waste_pct == 15 and ten.sheet == (1500, 6000)
    assert ten.sheets == math.ceil(5.5 * 1.15 / 9.0)
    three = sheets[3]
    assert three.area_m2 == pytest.approx(3 * 2 * 0.1) and three.sheet == (1250, 2500)
    assert three.mass_kg == pytest.approx(3 * 2 * 2.355 * 1.15)
    tube, = order.rolled
    assert tube.mass_kg == pytest.approx(300 * 1.05) and tube.pieces == 3
    bolt, = order.hardware
    assert bolt.qty == 3 + 5 and bolt.price is None
    axle, = order.bought
    assert axle.qty == 6 and axle.total == 6 * 45000
    text = purchase.summary_text(order)
    assert "Трейлер × 3" in text and "крепёж и стандартные — 8 шт." in text


def test_the_sheet_formats_come_from_the_plant_norms():
    norms = estimate.recommended()
    norms["sheet_formats"] = [{"max_thickness": 0, "width": 2000, "length": 6000}]
    order = purchase.build([(snap(cart()), 10)], norms)
    assert order.sheets[0].sheet == (2000, 6000) and order.sheets[0].sheets == math.ceil(5 * 1.15 / 12)


def test_old_norms_files_get_the_recommended_formats(tmp_path):
    (tmp_path / "plant_norms.json").write_text('{"rate": 1000}', encoding="utf-8")
    assert estimate.load_norms()["sheet_formats"] == estimate.RECOMMENDED["sheet_formats"]


def test_the_workbook(tmp_path):
    from openpyxl import load_workbook

    order = purchase.build([(snap(trailer()), 2)])
    path = purchase.save_xlsx(order, tmp_path / "Заявка.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames == ["Листы", "Прокат и трубы", "Крепёж и стандартные", "Покупные", "Состав заявки"]
    rows = [r for r in wb["Листы"].iter_rows(min_row=4, values_only=True)]
    assert rows[-1][0] == "Итого" and rows[-1][6] == sum(r.sheets for r in order.sheets)
    assert any("Без цены" in str(r[1]) for r in wb["Покупные"].iter_rows(values_only=True))


def test_the_purchase_window(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from desktop_browser.purchase_dialog import PurchaseDialog
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    dialog = PurchaseDialog()
    assert not dialog.ok.isEnabled()
    dialog.add(str(tmp_path / "900.000 - снимок.json"), 3)
    dialog.add(str(tmp_path / "910.000.a3d"))
    assert dialog.ok.isEnabled()
    assert dialog.products() == [(str(tmp_path / "900.000 - снимок.json"), 3), (str(tmp_path / "910.000.a3d"), 1)]


def test_details_without_a_material_are_not_ordered_as_steel_10():
    """KOMPAS gives a new part «Сталь 10»: the order says the material is not set."""
    top = Component(ASSEMBLY, "920.000", "Узел", "", 0, 0, "C:\m\920.000.a3d", children=[
        det("920.001", "Планка", "Сталь 10 ГОСТ 1050-2013", 5.0), det("920.002", "Втулка", "", 1.0)])
    rolled, = purchase.build([(snap(top), 2)]).rolled
    assert rolled.material == purchase.NO_MATERIAL and rolled.pieces == 4
