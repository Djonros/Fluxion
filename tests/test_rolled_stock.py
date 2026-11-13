"""Tubes and profiles are rolled stock, not sheets; their lengths reach the report, the estimate and the
purchase list; reading an assembly rebuilds a broken cache of the COM wrappers first."""
from __future__ import annotations

import pytest

from cad import assembly, compare, dfma, estimate, purchase, report
from cad.assembly import ASSEMBLY, DETAIL, AssemblyError, Component

TUBE_SQ = "Труба Д1 Н КВ 40х40х2 ГОСТ 18475-82"
TUBE_FRAC = "Труба $d 40x20x2 ГОСТ 8645-68;Ст2кп ГОСТ 13663-86 $"
ANGLE = "Уголок$d50x50x5 ГОСТ 8509-93;Ст3сп ГОСТ 535-2005$"
SHEET3 = "Лист$d3 ГОСТ 19903-2015;С235 ГОСТ 27772-2015$"


@pytest.fixture(autouse=True)
def _norms_in_tmp(tmp_path, monkeypatch):
    from cad import workload

    monkeypatch.setattr(report, "decisions_path", lambda: tmp_path / "report_decisions.json")
    monkeypatch.setattr(workload, "norms_path", lambda: tmp_path / "time_norms.json")
    monkeypatch.setattr(estimate, "norms_path", lambda: tmp_path / "plant_norms.json")


def det(name, material, kg=1.0, sheet_t=0.0, length=0.0, marking=""):
    c = Component(DETAIL, marking, name, material, kg * 1000, 1000, f"C:\\m\\{name}.m3d")
    c.sheet_thickness, c.length_mm = sheet_t, length
    return c


def product():
    """A frame: two tubes (one modeled as a sheet shell with a 2 mm wall), an angle, a plate."""
    return Component(ASSEMBLY, "100.000", "Рама", "", 0, 0, "C:\\m\\100.000.a3d", children=[
        det("Лонжерон@L = 1408.3 мм", TUBE_SQ, kg=3.3, length=1408.3, marking="100.001"),
        det("Лонжерон@L = 1408.3 мм", TUBE_SQ, kg=3.3, length=1408.3, marking="100.001"),
        det("Поперечина", TUBE_FRAC, kg=1.4, sheet_t=2.0, length=765, marking="100.002"),
        det("Стойка", ANGLE, kg=2.0, marking="100.003"),  # length not read
        det("Пластина", SHEET3, kg=1.0, sheet_t=3.0, marking="100.004"),
    ])


@pytest.mark.parametrize("material, is_rolled", [
    (TUBE_SQ, True), (TUBE_FRAC, True), (ANGLE, True),
    ("Швеллер$d10П ГОСТ 8240-97;Ст3$", True), ("Круг$d20 ГОСТ 2590-2006;Сталь 45$", True),
    ("Полоса 40х4 ГОСТ 103-2006 / Ст3", True), ("Профиль гнутый 60х40х3", True),
    (SHEET3, False), ("Сталь 3 ГОСТ 380-2005", False), ("", False),
])
def test_rolled_by_material(material, is_rolled):
    assert assembly.rolled(material) is is_rolled


def test_a_tube_with_a_sheet_body_is_not_a_sheet():
    assert not assembly.sheet_detail(det("Поперечина", TUBE_FRAC, sheet_t=2.0))
    assert assembly.sheet_detail(det("Пластина", SHEET3))
    assert assembly.sheet_detail(det("Косынка", "Сталь 3 ГОСТ 380-2005", sheet_t=4.0))  # a sheet body, a grade
    assert not assembly.sheet_detail(det("Ось", "Сталь 45 ГОСТ 1050-2013"))


def test_length_from_the_name_of_metal_structures():
    assert assembly.length_from_name("Направляющая дышла@L = 558.3 мм") == 558.3
    assert assembly.length_from_name("Стойка @L=78мм") == 78.0
    assert assembly.length_from_name("Пластина") == 0.0


def test_report_shows_lengths_and_metres_by_sortament(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    r = report.build(product())
    metal = {row["material"]: row for row in r.metal}
    tube = metal[assembly.material_text(TUBE_SQ)]
    assert tube["length_m"] == pytest.approx(2.8166) and tube["no_length"] == 0
    assert metal[assembly.material_text(ANGLE)]["no_length"] == 1
    assert metal[assembly.material_text(SHEET3)]["length_m"] == 0
    wb = openpyxl.load_workbook(report.save_xlsx(r, tmp_path / "r.xlsx"))
    ws = wb["Детали"]
    head = [c.value for c in ws[4]]
    assert head[-2:] == ["Длина 1 шт, мм", "Длина всего, м"]
    rows = {row[0]: row for row in ws.iter_rows(min_row=5, values_only=True) if row[0]}
    assert rows["100.001"][8] == 1408.3 and rows["100.001"][9] == pytest.approx(2.8166)
    assert rows["100.004"][8] is None  # a plate has no length
    assert any(isinstance(row[1], str) and row[1].startswith("Длина — у деталей из проката")
               for row in ws.iter_rows(values_only=True))
    ms = wb["Металлоёмкость"]
    assert [c.value for c in ms[4]][-1] == "Длина всего, м"
    tube_row = next(row for row in ms.iter_rows(values_only=True) if row[0] == assembly.material_text(TUBE_SQ))
    assert tube_row[6] == pytest.approx(2.8166)
    assert any(isinstance(row[0], str) and "без длины: 1 шт." in row[0] for row in ms.iter_rows(values_only=True))


def test_estimate_cuts_only_sheets():
    r = report.build(product())
    lines = [line for line in r.details]
    assert sum(l.qty_product for l in lines if estimate.sheet_detail(l.component)) == 1  # the plate only


def test_dfma_does_not_check_a_tube_as_a_sheet():
    assert dfma.sheet_problems(det("Поперечина", TUBE_FRAC, sheet_t=3.0)) == []


def test_purchase_list_orders_tubes_in_metres():
    snap = compare.snapshot(report.build(product()))
    items = {i["name"]: i for i in snap["items"] if i["kind"] == DETAIL}
    assert items["Поперечина"]["sheet_t"] == 0 and items["Поперечина"]["length_mm"] == 765
    assert items["Пластина"]["sheet_t"] == 3.0
    order = purchase.build([(snap, 2)])
    assert [(r.material, r.thickness) for r in order.sheets] == [(assembly.material_text(SHEET3), 3.0)]
    tube = next(r for r in order.rolled if r.material == assembly.material_text(TUBE_SQ))
    assert tube.length_m == pytest.approx(2 * 2.8166) and tube.no_length == 0
    assert next(r for r in order.rolled if r.material == assembly.material_text(ANGLE)).no_length == 2


# ── a broken cache of the COM wrappers while reading the assembly ───────────


def test_reading_rebuilds_a_broken_cache_and_tries_again(monkeypatch, tmp_path):
    pytest.importorskip("win32com.client")
    import win32com.client

    import dxfka.kompas.connection as connection

    path = tmp_path / "Установка поворотных замков.a3d"
    path.write_bytes(b"")
    calls = {"ensure": 0, "clear": 0, "read": 0}
    monkeypatch.setattr(connection, "ensure_wrappers", lambda log=None: calls.__setitem__("ensure", calls["ensure"] + 1) or True)
    monkeypatch.setattr(connection, "clear_com_cache", lambda: calls.__setitem__("clear", calls["clear"] + 1) or [])
    monkeypatch.setattr(win32com.client, "Dispatch", lambda progid: object())

    def read_once(app, p, with_area, log, cast):
        calls["read"] += 1
        if calls["read"] == 1:
            broken = AttributeError("module 'win32com.gen_py.69AC2981-37C0-4379-84FD-5DD2F3C0A520x0x1x0' "
                                    "has no attribute 'CLSIDToClassMap'")
            raise AssemblyError(f"КОМПАС-3D не смог прочитать сборку: {broken}") from broken
        return Component(ASSEMBLY, "532001.8401010", "Установка поворотных замков")

    monkeypatch.setattr(assembly, "_read", read_once)
    said: list[str] = []
    top = assembly.read(path, log=said.append)
    assert top.marking == "532001.8401010"
    assert calls == {"ensure": 2, "clear": 1, "read": 2}
    assert any("пересоздаю" in s for s in said)


def test_other_errors_are_not_retried(monkeypatch, tmp_path):
    pytest.importorskip("win32com.client")
    import win32com.client

    import dxfka.kompas.connection as connection

    path = tmp_path / "a.a3d"
    path.write_bytes(b"")
    monkeypatch.setattr(connection, "ensure_wrappers", lambda log=None: True)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda progid: object())
    tries = []

    def read_once(*args):
        tries.append(1)
        raise AssemblyError("КОМПАС-3D не открыл a.a3d.")

    monkeypatch.setattr(assembly, "_read", read_once)
    with pytest.raises(AssemblyError, match="не открыл"):
        assembly.read(path, log=lambda s: None)
    assert len(tries) == 1


class Stop(Exception):
    pass


def test_fix_and_welds_rebuild_the_cache_before_kompas(monkeypatch, tmp_path):
    pytest.importorskip("win32com.client")
    import dxfka.kompas.connection as connection
    from cad import fix, welds

    def ensure(log=None):
        raise Stop("ensure_wrappers called")

    monkeypatch.setattr(connection, "ensure_wrappers", ensure)
    with pytest.raises(Stop):
        fix._apply(tmp_path / "a.a3d", [], print, None)
    f = tmp_path / "b.a3d"
    f.write_bytes(b"")
    with pytest.raises(Stop):
        welds.compute(Component(ASSEMBLY, "1", "Рама", file=str(f)), log=print)


def test_ensure_wrappers_repairs_all_three_typelibs(monkeypatch):
    import dxfka.kompas.connection as connection

    asked: list[str] = []
    monkeypatch.setattr(connection, "ensure_module", lambda guid, log=None: asked.append(guid))
    assert connection.ensure_wrappers() is True
    assert asked == [connection.TYPELIB_API7, connection.TYPELIB_API5, connection.TYPELIB_CONSTANTS]


def test_broken_cache_errors_are_recognised():
    import dxfka.kompas.connection as connection

    assert connection.broken_cache_error(AttributeError("module 'win32com.gen_py.X' has no attribute 'MinorVersion'"))
    assert not connection.broken_cache_error(RuntimeError("КОМПАС-3D не открыл файл"))


def test_the_laser_package_leaves_tubes_out():
    from types import SimpleNamespace

    from cad import laser

    assert not laser.cut_from_sheet(SimpleNamespace(is_sheet=True, material=TUBE_FRAC))
    assert laser.cut_from_sheet(SimpleNamespace(is_sheet=False, material=SHEET3))
    assert laser.cut_from_sheet(SimpleNamespace(is_sheet=True, material=""))
