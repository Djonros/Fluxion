"""«Чертёж → деталь» (cad/drawing_input.py) on DXF files drawn here with ezdxf — no KOMPAS-3D needed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

from cad import drawing_input, text2cad


def plate(path: Path, width=200.0, height=100.0, holes=(), dim_width=False, texts=()) -> Path:
    """A rectangular plate: the outline as separate lines (as KOMPAS writes it), circle holes,
    a linear dimension of the width and texts."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        msp.add_line(a, b)
    for x, y, d in holes:
        msp.add_circle((x, y), d / 2)
    if dim_width:
        msp.add_linear_dim(base=(0, -20), p1=(0, 0), p2=(width, 0)).render()
    for t in texts:
        msp.add_text(t, dxfattribs={"height": 5}).set_placement((width / 2, height + 10))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def test_plate_summary(tmp_path):
    text = drawing_input.summarize(plate(tmp_path / "a.dxf", 200, 100, holes=[(50, 50, 20), (150, 50, 20)]))
    assert "[Чертёж a.dxf]" in text
    assert "Габарит: 200 × 100 мм" in text
    assert "Отверстия (окружности): 2 шт." in text
    assert "⌀20 — 2 шт., центры: (50, 50), (150, 50)" in text


def test_summary_of_a_round_flange(tmp_path):
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_circle((0, 0), 75)
    msp.add_circle((0, 0), 25)
    for i in range(6):
        import math

        angle = math.radians(i * 60)
        msp.add_circle((100 * math.cos(angle), 100 * math.sin(angle)), 5)
    path = tmp_path / "flange.dxf"
    doc.saveas(path)
    text = drawing_input.summarize(path)
    assert "Внешний контур: окружность ⌀150 мм." in text
    assert "Отверстия (округи)" not in text
    assert "⌀50 — 1 шт." in text
    assert "⌀10 — 6 шт." in text


def test_dims_and_texts_are_read(tmp_path):
    text = drawing_input.summarize(plate(tmp_path / "b.dxf", dim_width=True, texts=["Сталь 45", "пластина"]))
    assert "Линейные размеры: 200." in text
    assert "Надписи: «Сталь 45», «пластина»." in text


def test_broken_file_is_a_problem_not_an_exception(tmp_path):
    path = tmp_path / "bad.dxf"
    path.write_text("это не DXF", encoding="utf-8")
    blocks, problems = drawing_input.blocks([tmp_path / "bad.dxf"])
    assert not blocks and len(problems) == 1 and "bad.dxf" in problems[0]


def test_two_files_are_joined(tmp_path):
    a = plate(tmp_path / "a.dxf", 100, 50)
    b = plate(tmp_path / "b.dxf", 300, 200, holes=[(50, 50, 20)])
    summary, problems = drawing_input.blocks([a, b])
    assert not problems
    assert "[Чертёж a.dxf]" in summary and "[Чертёж b.dxf]" in summary


def test_conversation_prepends_drawing_to_the_description():
    PLATE = {"template": "plate", "name": "Пластина",
             "params": {"length": 200, "width": 100, "thickness": 8}, "material": "сталь 3"}
    class _Recorder:
        def generate(self, messages, gen=None):
            return json.dumps(PLATE, ensure_ascii=False)
    conversation = text2cad.Conversation(_Recorder(), drawing="[Чертёж a.dxf]\nГабарит: 200 × 100 мм.")
    conversation.step("деталь по чертежу, толщина 8")
    # the description read by the model must contain the drawing summary
    assert conversation.description.startswith("[Чертёж a.dxf]\nГабарит: 200 × 100 мм.")
    assert "Описание: деталь по чертежу" in conversation.description


def test_conversation_drawing_used_once():
    PLATE = {"template": "plate", "name": "Пластина", "params": {"length": 200, "width": 100, "thickness": 8}}
    class _Recorder:
        def generate(self, messages, gen=None):
            return json.dumps(PLATE, ensure_ascii=False)
    conversation = text2cad.Conversation(_Recorder(), drawing="СВОДКА")
    conversation.step("пластина по чертежу")
    conversation.step("толщина 10, сталь 45")
    assert conversation.description.count("СВОДКА") == 1


def test_empty_geometry_raises(tmp_path):
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_text("только текст", dxfattribs={"height": 5}).set_placement((0, 0))
    path = tmp_path / "empty.dxf"
    doc.saveas(path)
    with pytest.raises(drawing_input.DrawingInputError):
        drawing_input.summarize(path)
