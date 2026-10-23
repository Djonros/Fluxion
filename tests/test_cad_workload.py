"""Hours of design by complexity and the surface for painting (cad/workload.py)."""
from __future__ import annotations

import json

import pytest

from cad import report, welds, workload
from cad.assembly import ASSEMBLY, DETAIL, Component


@pytest.fixture(autouse=True)
def _files_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "norms_path", lambda: tmp_path / "time_norms.json")
    monkeypatch.setattr(report, "decisions_path", lambda: tmp_path / "report_decisions.json")


def det(name, material="Лист$dБ-ПУ-О-10 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$", m2=1.0, features=()):
    return Component(DETAIL, f"1.{name}", name, material, 10000, m2 * 10000, rf"C:\m\{name}.m3d",
                     features=list(features))


def asm(marking, children):
    return Component(ASSEMBLY, marking, marking, "", 0, 0, rf"C:\m\{marking}.a3d", children=children)


SIMPLE = ["Эскиз:1", "Элемент выдавливания:1"]
MEDIUM = ["Смещенная плоскость:1", "Эскиз:1", "Листовое тело:1", "Сгиб:1", "Сгиб:2", "Эскиз:2",
          "Вырезать выдавливанием:1", "Отверстие:1"]
COMPLEX = ["Эскиз:1", "Элемент выдавливания:1"] + [f"Скругление:{n}" for n in range(1, 12)]


def test_operations_leave_out_sketches_and_auxiliary_geometry():
    assert workload.operations(SIMPLE) == (1, 1)
    assert workload.operations(MEDIUM) == (5, 2)
    assert workload.operations(COMPLEX) == (12, 1)


def test_categories_from_the_norms_file(tmp_path):
    norms = workload.load_norms()
    assert (tmp_path / "time_norms.json").is_file()  # written once so the department can edit it
    assert workload.category(1, norms) == ("простая", 0.5)
    assert workload.category(5, norms) == ("средняя", 1.5)
    assert workload.category(12, norms) == ("сложная", 3.0)
    edited = dict(norms, detail=[{"category": "любая", "max_operations": None, "hours": 2}])
    (tmp_path / "time_norms.json").write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")
    assert workload.category(40, workload.load_norms()) == ("любая", 2.0)


def test_workload_counts_each_detail_once():
    unit = asm("1.100", [det("a", features=SIMPLE), det("b", features=MEDIUM)])
    top = asm("1.000", [unit, unit, det("c", features=COMPLEX), det("c", features=COMPLEX)])
    rows = workload.workload(report.build(top))
    details = {r.title: r.hours for r in rows if r.kind == "Деталь"}
    assert details == {"1.a a": 0.5, "1.b b": 1.5, "1.c c": 3.0}  # the copies are not work
    assemblies = {r.title: (r.operations, r.hours) for r in rows if r.kind != "Деталь"}
    assert assemblies["1.000 1.000"] == (4, 0.33)  # 0.25 + 4 × 0.02
    assert assemblies["1.100 1.100"] == (2, 0.29)


def test_paint_surface():
    stainless = det("s", "Лист$d2 ГОСТ 19904-90;12Х18Н10Т ГОСТ 5582-75$", m2=2.0)
    rubber = det("r", "Смесь резиновая 3063 ТУ 38", m2=0.5)
    unit = asm("1.100", [det("wall", m2=3.0), det("rib", m2=1.0)])
    r = report.build(asm("1.000", [unit, unit, stainless, rubber]))
    r.welds = [welds.WeldedAssembly(unit, 2, [welds.Joint("rib", "wall", 10, 600, area_mm2=3000)])]
    p = workload.paint(r)
    assert p.details_m2 == pytest.approx(2 * 3 + 2 * 1 + 2 + 0.5)
    assert p.not_painted_m2 == pytest.approx(2.5)
    assert p.joints_m2 == pytest.approx(2 * 3000 * 2 / 1e6)  # both faces, two units
    assert p.paint_m2 == pytest.approx(8 - 0.012)
    assert workload.painted("Сталь 20 ГОСТ 1050-2013") and not workload.painted("АМг6")
    assert not workload.painted("Лист$d0,8 ГОСТ 19904-90;Оцинкованный 08пс$")


def test_report_has_paint_and_work_sheets(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    r = report.build(asm("1.000", [det("a", features=SIMPLE), det("b", features=COMPLEX)]))
    wb = openpyxl.load_workbook(report.save_xlsx(r, tmp_path / "r.xlsx"))
    work = list(wb["Трудоёмкость"].iter_rows(values_only=True))
    total = next(row for row in work if row[0] == "Итого на модель изделия")
    assert total[5] == pytest.approx(0.5 + 3.0 + 0.25 + 2 * 0.02)
    paint_rows = list(wb["Покраска"].iter_rows(values_only=True))
    assert any(row[0] == "Под дробеструйную обработку и окраску" and row[1] == pytest.approx(2.0)
               for row in paint_rows)
    text = report.summary_text(r)
    assert "Под дробеструй и окраску: 2.0 м²" in text and "Трудоёмкость модели по нормам: 3.8 ч" in text
