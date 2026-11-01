"""«Смета» of an assembly (cad/estimate.py) and the window «Нормы и цены завода» —
no KOMPAS-3D needed."""
from __future__ import annotations

import json

import pytest

from cad import estimate, report
from cad.assembly import ASSEMBLY, DETAIL, OTHER, STANDARD, Component

SHEET3 = "Лист$dБ-ПН-О-3 ГОСТ 19903-2015;Ст3сп ГОСТ 14637-89$"
SHEET8_09G2S = "Лист$dБ-ПУ-О-8 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$"


@pytest.fixture(autouse=True)
def _files_in_tmp(tmp_path, monkeypatch):
    from cad import workload

    monkeypatch.setattr(report, "decisions_path", lambda: tmp_path / "report_decisions.json")
    monkeypatch.setattr(workload, "norms_path", lambda: tmp_path / "time_norms.json")
    monkeypatch.setattr(estimate, "norms_path", lambda: tmp_path / "plant_norms.json")


def det(marking, name, material, kg, m2=0.1, **extra):
    return Component(DETAIL, marking, name, material, kg * 1000, m2 * 10000, f"C:\\m\\{marking}.m3d", **extra)


def product():
    return Component(ASSEMBLY, "500.000", "Рама", "", 60000, 0, "C:\\m\\500.000.a3d", children=[
        det("500.001", "Косынка", SHEET3, 2.0, features=["Листовое тело:1", "Сгиб:1", "Сгиб:2"]),
        det("500.001", "Косынка", SHEET3, 2.0, features=["Листовое тело:1", "Сгиб:1", "Сгиб:2"]),
        det("500.002", "Стенка", SHEET8_09G2S, 30.0, m2=1.0),
        det("500.003", "Ось", "Круг 40 ГОСТ 2590-2006 / Сталь 45 ГОСТ 1050-2013", 10.0, m2=0.2),
        det("500.004", "Втулка", "", 1.0),  # no material
        Component(STANDARD, "", "Болт М12-6gx40.58.016 ГОСТ 7798-70", "", 50, 0, "C:\\lib\\b.m3d"),
        Component(STANDARD, "", "Гайка М12-6H.5.016 ГОСТ 5915-70", "", 20, 0, "C:\\lib\\n.m3d"),
        Component(OTHER, "", "Ось 35т", "", 300000, 0, "C:\\m\\axle.a3d", section="Прочие изделия"),
    ])


def test_the_recommended_norms_are_written_once_and_can_be_corrected(tmp_path):
    norms = estimate.load_norms()
    assert (tmp_path / "plant_norms.json").is_file()
    assert estimate.changed(norms) == [] and estimate.basis(norms) == "по рекомендуемым нормам"
    norms["rate"] = 900
    norms["operations"][2]["hours"] = 0.55  # welding
    estimate.save_norms(norms)
    again = estimate.load_norms()
    assert estimate.changed(again) == ["Ставка нормо-часа", "Сварка"]
    assert estimate.basis(again) == "по нормам завода"


def test_a_hand_edited_file_is_read_safely(tmp_path):
    (tmp_path / "plant_norms.json").write_text(json.dumps({
        "rate": "1 000,5", "metal": [{"match": "[", "name": "Кривое правило", "price_kg": "abc"}],
        "operations": [{"key": "weld", "hours": "0,5"}], "items": [{"match": "", "price": 5}]}), encoding="utf-8")
    norms = estimate.load_norms()
    assert norms["rate"] == 1000.5 and norms["metal"][0]["price_kg"] == 0
    assert [o["key"] for o in norms["operations"]] == [k for k, _n, _u in estimate.OPERATIONS]
    assert norms["operations"][2]["hours"] == 0.5 and norms["items"] == []
    assert estimate.metal_rule("лист [3]", norms) is not None  # a bad regex is taken as plain text


def test_metal_rules_first_match_wins():
    norms = estimate.recommended()
    assert estimate.metal_rule(SHEET8_09G2S, norms)["name"] == "Лист низколегированный"
    assert estimate.metal_rule(SHEET3, norms)["name"] == "Лист стальной"
    assert estimate.metal_rule("Лист 2 ГОСТ 19904 / 12Х18Н10Т", norms)["name"] == "Нержавеющая сталь"
    assert estimate.metal_rule("Круг 40 ГОСТ 2590-2006 / Сталь 45", norms)["name"] == "Круг"
    assert estimate.metal_rule("Капролон", norms)["name"] == "Прочий металл"  # the empty rule takes anything


def test_the_estimate_of_a_product():
    norms = estimate.recommended()
    norms["items"] = [{"match": "Ось 35т", "price": 45000}, {"match": "Болт М12", "price": 30}]
    est = estimate.build(report.build(product()), norms)
    metal = {r.name: r for r in est.rows if r.section == estimate.METAL}
    sheet = next(r for name, r in metal.items() if "Ст3сп" in name)
    assert sheet.qty == pytest.approx(4.0 * 1.15) and sheet.price == 90.0  # two gussets + 15 % waste
    assert metal["Материал не указан"].missing
    hardware = {r.name: r for r in est.rows if r.section == estimate.HARDWARE}
    assert hardware["Болт М12-6gx40.58.016 ГОСТ 7798-70"].price == 30
    nut = hardware["Гайка М12-6H.5.016 ГОСТ 5915-70"]
    assert nut.note == "по массе" and nut.price == pytest.approx(0.02 * 300)
    bought = next(r for r in est.rows if r.section == estimate.BOUGHT)
    assert bought.total == 45000
    work = {r.name: r for r in est.rows if r.section == estimate.WORK}
    assert work["Резка листа"].qty == 3  # two gussets and the wall
    assert work["Гибка"].qty == 4  # two bends on each gusset
    assert work["Слесарная обработка"].qty == 2  # the axle and the bush
    assert work["Сварка"].missing and "нет данных" in work["Сварка"].note  # no welds read: no guess
    assert work["Гибка"].hours == pytest.approx(4 * 0.03) and work["Гибка"].price == 1200
    assert est.total == pytest.approx(est.subtotal())  # no overhead, no profit by default
    line = estimate.summary_line(est)
    assert line.startswith("Себестоимость ≈") and "не задано: 2 поз." in line and "по нормам завода" in line  # prices added


def test_welding_is_scaled_by_the_leg():
    from types import SimpleNamespace

    from cad.welds import Joint

    welds = [SimpleNamespace(qty_product=2, joints=[Joint("a", "b", 6, 1000), Joint("a", "c", 12, 500),
                                                     Joint("b", "c", 3, 1000, count=2)])]
    # 1 m at leg 6, 0.5 m at leg 12 counts 4×, 2 m at leg 3 counts 0.6× — on two assemblies
    assert estimate.weld_metres(SimpleNamespace(welds=welds)) == pytest.approx(2 * (1 + 0.5 * 4 + 2 * 0.6))


def test_overhead_and_profit():
    est = estimate.Estimate([estimate.Row(estimate.METAL, "Лист", 10, "кг", 100)], overhead_pct=20, profit_pct=10)
    assert est.subtotal() == 1000 and est.overhead == 200 and est.profit == pytest.approx(120)
    assert est.total == pytest.approx(1320)


def test_the_sheet_smeta_in_the_workbook(tmp_path):
    from openpyxl import load_workbook

    r = report.build(product())
    path = report.save_xlsx(r, tmp_path / "r.xlsx")
    ws = load_workbook(path)["Смета"]
    values = [[c for c in row] for row in ws.iter_rows(values_only=True)]
    assert "по рекомендуемым нормам" in str(values[1][0])
    batch = next(i for i, row in enumerate(values, 1) if row[0] == "Изделий в партии")
    assert values[batch - 1][2] == 1
    assert values[batch][0] == "Себестоимость партии" and str(values[batch][6]).startswith("=G")
    assert any(row[0] == "Не задано" for row in values)


def test_the_norms_go_to_excel_and_back(tmp_path):
    norms = estimate.recommended()
    norms["rate"] = 950
    norms["items"] = [{"match": "Ось 35т", "price": 45000}]
    norms["operations"][0]["hours"] = 0.2
    path = estimate.export_xlsx(norms, tmp_path / "n.xlsx")
    back = estimate.import_xlsx(path, estimate.recommended())
    assert back == estimate.normalize(norms)


def test_the_norms_window(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from desktop_browser.norms_dialog import NormsDialog
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    QApplication.instance() or QApplication([])
    dialog = NormsDialog()
    assert "рекомендуемые" in dialog.state.text()
    weld = next(r for r in range(dialog.work.rowCount()) if dialog.work.item(r, 0).data(0x0100) == "weld")
    dialog.work.item(weld, 2).setText("0,55")
    assert dialog.work.item(weld, 2).font().bold() and "Сварка" in dialog.state.text()
    dialog.general["rate"].setValue(900)
    dialog.save()
    saved = estimate.load_norms()
    assert saved["rate"] == 900 and saved["operations"][2]["hours"] == 0.55
    dialog.deleteLater()
    dialog = NormsDialog()
    dialog.reset_row(dialog.work, weld)
    assert not dialog.work.item(weld, 2).font().bold()
    monkeypatch.setattr("desktop_browser.norms_dialog.QMessageBox.question", lambda *a, **k: QMessageBox.Yes)
    dialog.reset_all()
    assert dialog.collect() == estimate.recommended() and "рекомендуемые" in dialog.state.text()
    dialog.deleteLater()  # not left to the end of the run: Qt tears it down in order
