"""Инженерный калькулятор, third stage: pins and lugs, hydraulic cylinders, the axle loads
of a trailer — checked against hand calculations."""
from __future__ import annotations

import math

import pytest

import calc
from calc import axles, cylinder, pins
from calc.core import CalcError

G = 9.80665


def check(result, title):
    return next(c for c in result.checks if c.title.startswith(title))


# ── pins and lugs ────────────────────────────────────────────────────────────


def test_pin_in_a_fork_by_hand():
    r = pins.calculate({"force": "12 тс", "diameter": 40, "lug": 30, "cheek": 20, "gap": 2, "width": 100, "edge": 60,
                        "kd": 1.5})
    F = 1.5 * 12 * G * 1e3
    assert check(r, "Срез пальца").actual == pytest.approx(F / (2 * math.pi * 40 ** 2 / 4))
    assert check(r, "Изгиб пальца").actual == pytest.approx(F / 2 * (2 + 10 + 7.5) / (0.1 * 40 ** 3))
    assert check(r, "Смятие в проушине").actual == pytest.approx(F / (40 * 30))
    assert check(r, "Разрыв проушины").actual == pytest.approx(F / (60 * 30))
    assert check(r, "Срез перемычки за").actual == pytest.approx(F / (2 * 40 * 30))
    assert check(r, "Изгиб пальца").limit == 785  # 40Х
    assert check(r, "Смятие в проушине").limit == 235  # Ст3 thicker than 20 mm
    assert r.status == "ok"


def test_a_weak_pin_gets_the_next_diameter():
    r = pins.calculate({"force": "12 тс", "diameter": 25, "lug": 30, "scheme": "односрезный", "kd": 1.5})
    assert r.status == "fail"
    assert r.suggestions and r.suggestions[0].startswith("палец ⌀")
    bigger = int(r.suggestions[0].split("⌀")[1].split()[0])
    again = pins.calculate({"force": "12 тс", "diameter": bigger, "lug": 30, "scheme": "односрезный", "kd": 1.5})
    assert min(c.margin for c in again.checks if "пальца" in c.title) >= 1.5


def test_a_lug_too_narrow_is_an_error():
    with pytest.raises(CalcError):
        pins.calculate({"force": "5 тс", "diameter": 40, "lug": 20, "width": 40})


# ── hydraulic cylinders ──────────────────────────────────────────────────────


def test_cylinder_forces_and_rod_buckling_by_hand():
    r = cylinder.calculate({"bore": 100, "rod": 50, "pressure": "160 бар", "load": "10 тс", "length": 1800})
    push = 16 * math.pi * 100 ** 2 / 4
    pull = 16 * math.pi * (100 ** 2 - 50 ** 2) / 4
    steps = {s.title: s.value for s in r.steps}
    assert steps["Усилие толкания"] == pytest.approx(push / 1e3)
    assert steps["Усилие тяги"] == pytest.approx(pull / 1e3)
    assert steps["Давление под нагрузку (толкание)"] == pytest.approx(10 * G * 1e3 / (math.pi * 100 ** 2 / 4))
    critical = math.pi ** 2 * 206000 * math.pi * 50 ** 4 / 64 / 1800 ** 2
    buckling = check(r, "Устойчивость штока")
    assert buckling.limit == pytest.approx(critical / 1e3) and buckling.actual == pytest.approx(push / 1e3)
    assert r.status == "low" and r.suggestions[0].startswith("шток ⌀")


def test_a_load_the_pressure_cannot_push():
    r = cylinder.calculate({"bore": 63, "rod": 28, "pressure": "16", "load": "8 тс"})
    assert check(r, "Усилие толкания").margin < 1
    assert any(s.startswith("поршень ⌀80") for s in r.suggestions)
    assert "не проверен" in " ".join(r.notes)  # no length: the rod buckling is not checked


def test_bar_and_atm_are_pressures():
    from calc import units

    assert units.parse("160 бар", units.STRESS) == pytest.approx(16)
    assert units.parse("100 атм", units.STRESS) == pytest.approx(10.1325)


# ── axle loads of a trailer ──────────────────────────────────────────────────


SEMI = {"frame": "6 т", "frame_x": 5500, "cargo": "30 т", "cargo_x": 7000, "axles": "6000; 7310; 8620",
        "axle_limit": "10 тс", "coupling_limit": "12 тс", "track": 2040, "height": 2100}


def test_semitrailer_by_hand():
    r = axles.calculate(SEMI)
    x = (6 * 5500 + 30 * 7000) / 36
    group = 36 * x / 7310
    steps = {s.title: s.value for s in r.steps}
    assert steps["Центр тяжести всего прицепа от сцепки"] == pytest.approx(x)
    assert steps["Нагрузка на оси"] == pytest.approx(group)
    assert steps["Нагрузка на сцепку"] == pytest.approx(36 - group)
    assert check(r, "Нагрузка на ось 1").actual == pytest.approx(group / 3)
    assert r.status == "fail"
    # the cargo between 4748 and 6210 mm from the coupling: every axle ≤ 10 t, the coupling ≤ 12 t
    assert r.suggestions == ["центр тяжести груза от 4748 до 6210 мм от сцепки — нагрузки в норме"]
    moved = axles.calculate({**SEMI, "cargo_x": 6100})
    assert all(c.margin >= 1 for c in moved.checks if c.title.startswith("Нагрузка"))
    roll = check(r, "Устойчивость против опрокидывания")
    assert roll.limit == pytest.approx(2040 / 4200) and roll.required == 1.25


def test_drawbar_trailer_with_a_front_axle():
    r = axles.calculate({"frame": "3 т", "frame_x": 3000, "cargo": "8 т", "cargo_x": 3500, "axles": "1500; 5000",
                         "coupling": "нет", "axle_limit": "6 тс"})
    x = (3 * 3000 + 8 * 3500) / 11
    rear = 11 * (x - 1500) / 3500
    assert check(r, "Нагрузка на ось 2").actual == pytest.approx(rear)
    assert check(r, "Нагрузка на ось 1").actual == pytest.approx(11 - rear)
    assert not any("сцепк" in c.title.lower() for c in r.checks)


def test_cargo_behind_the_axles_lifts_the_coupling():
    r = axles.calculate({"frame": "2 т", "frame_x": 3000, "cargo": "5 т", "cargo_x": 6000, "axles": "4000",
                         "axle_limit": "10 тс"})
    assert check(r, "Сцепка не отрывается").margin == 0 and r.status == "fail"


def test_the_new_tabs_are_in_the_calculator():
    assert [calc.CALCS[k][0] for k in ("pin", "cylinder", "axles")] == ["Пальцы", "Гидроцилиндр", "Развесовка"]
    from calc.chat import system_prompt

    prompt = system_prompt()
    assert 'calc = "cylinder"' in prompt and 'calc = "axles"' in prompt and "  diameter: Диаметр пальца d" in prompt


def test_the_report_of_a_new_calculation(tmp_path):
    from openpyxl import load_workbook

    from calc import report

    r = axles.calculate(SEMI)
    path = report.save_xlsx(r, tmp_path / "развесовка.xlsx")
    text = " ".join(str(c.value) for ws in load_workbook(path) for row in ws.iter_rows() for c in row if c.value)
    assert "Нагрузки на оси" in text and "Развесовка прицепа" in text
