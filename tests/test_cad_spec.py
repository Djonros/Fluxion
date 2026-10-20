import json
import math

import pytest

from cad import materials, sheet, spec, text2cad


@pytest.mark.parametrize("key", list(spec.TEMPLATES))
def test_every_template_builds_with_defaults(key):
    part, _ = spec.build(key, {})
    spec.validate_ops(part["ops"])
    assert part["ops"][0]["op"] in ("sketch", "sheet", "sheet_box")


@pytest.mark.parametrize("key", list(spec.TEMPLATES))
def test_template_example_builds(key):
    _, example = spec.TEMPLATES[key].example
    spec.build(key, example)


def test_plate_holes_in_corners():
    part, _ = spec.build("plate", {"length": 100, "width": 60, "hole_d": 8, "hole_offset": 10})
    circles = [s["circle"] for s in part["ops"][0]["shapes"] if "circle" in s]
    assert sorted((c["cx"], c["cy"]) for c in circles) == [(-40, -20), (-40, 20), (40, -20), (40, 20)]


def test_flange_bolts_must_fit():
    with pytest.raises(spec.SpecError):
        spec.build("flange", {"outer_d": 100, "inner_d": 80, "bolt_count": 4, "bolt_d": 20, "bolt_circle_d": 90})


def test_shaft_steps_stack_along_z():
    part, _ = spec.build("shaft", {"steps": [{"d": 40, "length": 60}, {"d": "30", "length": "80,5"}]})
    sketches = [op for op in part["ops"] if op["op"] == "sketch"]
    assert [s["z"] for s in sketches] == [0, 60]
    assert part["params"]["steps"][1]["length"] == 80.5


def test_bad_numbers_rejected():
    with pytest.raises(spec.SpecError):
        spec.build("bushing", {"outer_d": "много"})
    with pytest.raises(spec.SpecError):
        spec.build("bushing", {"outer_d": 20, "inner_d": 30})
    with pytest.raises(spec.SpecError):
        spec.build("plate", {"thickness": 0})


def test_unknown_template():
    with pytest.raises(spec.SpecError):
        spec.build("gear", {})


def test_assumptions_listed():
    _, assumed = spec.build("bushing", {"outer_d": 30})
    assert any("длина" in a for a in assumed)


def test_validate_ops_rejects_cut_without_sketch():
    with pytest.raises(spec.SpecError):
        spec.validate_ops([{"op": "cut", "through": True}])


def test_plate_end_holes_cut_inwards_from_both_ends():
    part, _ = spec.build("plate", {"length": 100, "width": 60, "thickness": 20, "end_hole_d": 8, "end_hole_depth": 30})
    side = [op for op in part["ops"] if op.get("plane") == "YOZ"]
    assert [(op["offset"], op["shapes"][0]["circle"]["cy"]) for op in side] == [(50, 10), (-50, 10)]
    cuts = [op for op in part["ops"] if op["op"] == "cut"]
    assert [(op["depth"], op["direction"]) for op in cuts] == [(30, "-"), (30, "+")]


def test_plate_end_hole_depth_defaults_to_two_diameters():
    part, _ = spec.build("plate", {"thickness": 20, "end_hole_d": 6})
    assert all(op["depth"] == 12 for op in part["ops"] if op["op"] == "cut")


def test_plate_end_holes_must_fit():
    with pytest.raises(spec.SpecError):
        spec.build("plate", {"thickness": 5, "end_hole_d": 6})
    with pytest.raises(spec.SpecError):
        spec.build("plate", {"length": 100, "thickness": 20, "end_hole_d": 6, "end_hole_depth": 50})
    with pytest.raises(spec.SpecError):  # runs into the corner holes
        spec.build("plate", {"width": 30, "thickness": 20, "hole_d": 8, "hole_offset": 10,
                             "end_hole_d": 8, "end_hole_depth": 20})


def test_plate_chamfer_on_outer_contour():
    part, _ = spec.build("plate", {"length": 100, "width": 60, "thickness": 10, "chamfer": 1})
    chamfer = part["ops"][-1]
    assert chamfer["op"] == "chamfer" and chamfer["size"] == 1
    assert sorted(map(tuple, chamfer["points"])) == sorted(
        (x, y, z) for z in (0, 10) for x, y in ((50, 0), (-50, 0), (0, 30), (0, -30)))
    with pytest.raises(spec.SpecError):
        spec.build("plate", {"thickness": 2, "chamfer": 1})


def test_bushing_chamfer_on_both_ends_inside_and_out():
    part, _ = spec.build("bushing", {"outer_d": 30, "inner_d": 20, "length": 50, "chamfer": 1})
    points = part["ops"][-1]["points"]
    assert len(points) == 4
    assert sorted({round(math.hypot(x, y), 6) for x, y, _ in points}) == [10, 15]
    assert sorted({z for *_, z in points}) == [0, 50]
    with pytest.raises(spec.SpecError):
        spec.build("bushing", {"outer_d": 30, "inner_d": 26, "chamfer": 1.5})


def test_shaft_pin_hole_and_chamfer():
    part, _ = spec.build("shaft", {"steps": [{"d": 40, "length": 60}, {"d": 25, "length": 40}],
                                   "pin_hole_d": 6, "pin_hole_z": 80, "chamfer": 1})
    sketch = next(op for op in part["ops"] if op.get("plane") == "XOZ")
    assert sketch["shapes"][0]["circle"] == {"cx": 0, "cy": 80, "d": 6}
    cut = part["ops"][part["ops"].index(sketch) + 1]
    assert cut == {"op": "cut", "through": True, "direction": "both"}
    points = part["ops"][-1]["points"]
    assert [(round(math.hypot(x, y), 6), z) for x, y, z in points] == [(20, 0), (12.5, 100)]


def test_shaft_pin_hole_defaults_to_middle_and_must_fit():
    part, _ = spec.build("shaft", {"steps": [{"d": 30, "length": 100}], "pin_hole_d": 5})
    assert next(op for op in part["ops"] if op.get("plane") == "XOZ")["shapes"][0]["circle"]["cy"] == 50
    with pytest.raises(spec.SpecError):
        spec.build("shaft", {"steps": [{"d": 30, "length": 60}, {"d": 10, "length": 40}],
                             "pin_hole_d": 12, "pin_hole_z": 80})
    with pytest.raises(spec.SpecError):
        spec.build("shaft", {"steps": [{"d": 30, "length": 60}], "pin_hole_d": 6, "pin_hole_z": 59})


def test_validate_ops_checks_planes_directions_and_chamfers():
    sketch = {"op": "sketch", "shapes": [{"circle": {"cx": 0, "cy": 0, "d": 5}}]}
    body = [{"op": "sketch", "z": 0, "shapes": [{"rect": {"cx": 0, "cy": 0, "w": 10, "h": 10}}]},
            {"op": "extrude", "depth": 5}]
    with pytest.raises(spec.SpecError):
        spec.validate_ops(body + [dict(sketch, plane="ABC"), {"op": "cut", "through": True}])
    with pytest.raises(spec.SpecError):
        spec.validate_ops(body + [dict(sketch, plane="XOZ"), {"op": "cut", "through": True, "direction": "up"}])
    with pytest.raises(spec.SpecError):
        spec.validate_ops([{"op": "chamfer", "size": 1, "points": [[0, 0, 0]]}])
    with pytest.raises(spec.SpecError):
        spec.validate_ops(body + [{"op": "chamfer", "size": 1, "points": [[0, 0]]}])
    spec.validate_ops(body + [dict(sketch, plane="YOZ", offset=5), {"op": "cut", "depth": 2, "direction": "-"},
                              {"op": "chamfer", "size": 0.5, "points": [[5, 0, 5]]}])


def test_describe_lists_sizes_and_defaults():
    part, assumed = spec.build("bushing", {"outer_d": 30, "chamfer": 1})
    text = text2cad.describe(part, assumed)
    assert text.startswith("Шаблон: Втулка.") and "фаска" in text and "по умолчанию" in text


def test_text2cad_with_stub_backend():
    class Stub:
        def generate(self, messages, gen=None):
            assert gen.json_schema == text2cad.SCHEMA
            assert "flange" in messages[0]["content"]
            return 'Вот: {"template": "flange", "name": "Фланец Ду50", "params": {"outer_d": 150, "inner_d": 50, "thickness": 15, "bolt_count": 6, "bolt_d": 11, "bolt_circle_d": 110}}'

    part, _ = text2cad.parse("фланец 150", Stub())
    assert part["name"] == "Фланец Ду50"
    assert sum("circle" in s for s in part["ops"][0]["shapes"]) == 8
    json.dumps(part)


# ── desktop app: "/компас ..." and the menu item ──────────────────────────────

def test_cad_command_prefixes():
    pytest.importorskip("PySide6")
    from desktop_browser.app import cad_command

    assert cad_command("/компас втулка 30 на 20") == "втулка 30 на 20"
    assert cad_command("/KOMPAS  вал 40") == "вал 40"
    assert cad_command("/компас") == ""
    assert cad_command("/компасы") is None
    assert cad_command("сделай втулку") is None




class _ScriptedBackend:
    """Answers model calls in turn (the last answer repeats); keeps the requests."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def generate(self, messages, gen=None):
        self.requests.append(messages[-1]["content"])
        answer = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


BUSHING_30_20 = {"template": "bushing", "name": "Втулка", "params": {"outer_d": 30, "inner_d": 20}}
BUSHING_FULL = {"template": "bushing", "name": "Втулка",
                "params": {"outer_d": 30, "inner_d": 20, "length": 50}, "material": "нержавейка"}


# ── materials ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, short", [
    ("сталь 45", "Сталь 45"), ("из стали 45", "Сталь 45"), ("Ст3", "Ст3"), ("сталь 20", "Сталь 20"),
    ("нержавейка", "12Х18Н10Т (нержавейка)"), ("12X18H10T", "12Х18Н10Т (нержавейка)"), ("40x", "40Х"),
    ("AISI 304", "AISI 304"), ("алюминий", "Д16Т (алюминий)"), ("Д16", "Д16Т (алюминий)"), ("АМг6", "АМг6"),
    ("АМг3", "лист АМг3"), ("латунь", "латунь ЛС59-1"), ("бронзовая", "бронза БрАЖ9-4"), ("капролон", "капролон"),
    ("тефлон", "фторопласт-4"), ("оцинковка", "оцинковка"), ("09Г2С", "09Г2С"), ("08пс", "08пс"),
    ("чугун", "чугун СЧ20"), ("просто сталь", "Сталь 45"),
])
def test_find_material(text, short):
    assert spec.find_material(text).short == short


@pytest.mark.parametrize("text", ["медь", "сталь 30", "лист 3 мм", "уголок 40х30", "втулка 30 на 20 длиной 50", ""])
def test_find_material_does_not_guess(text):
    assert spec.find_material(text) is None


def test_material_densities_are_plausible():
    assert all(1 < m.density < 9 for m in materials.load())
    assert materials.default("solid").short == "Сталь 45"
    assert materials.default("sheet").short == "лист Ст3"


def test_build_carries_material():
    part, _ = spec.build("bushing", {}, material=spec.find_material("бронза"))
    assert part["material"] == {"name": "Бронза БрАЖ9-4 ГОСТ 18175-78", "short": "бронза БрАЖ9-4", "density": 7.6}
    assert "Материал: бронза БрАЖ9-4 (7.6 г/см³)" in text2cad.describe(part, [])
    assert "material" not in spec.build("bushing", {})[0]


def test_missing_main_sizes():
    assert spec.missing_params("bushing", {"outer_d": 30, "inner_d": 20}) == ["длина"]
    assert spec.missing_params("flange", {"inner_d": 50}) == ["наружный диаметр", "толщина"]
    assert spec.missing_params("shaft", {"steps": []}) == ["ступени"]
    assert spec.missing_params("bushing", {"outer_d": 30, "inner_d": 0, "length": 5}) == []
    assert all(t.required for t in spec.TEMPLATES.values())


def test_mass_text():
    from cad.kompas import BuildResult

    assert BuildResult(19634.95, 154.134).mass_text() == "154 г"
    assert BuildResult(1, 1534.0).mass_text() == "1.53 кг"
    assert BuildResult(None, None).mass_text() == ""


# ── questions ────────────────────────────────────────────────────────────────

def test_conversation_builds_at_once_when_all_is_given():
    backend = _ScriptedBackend(BUSHING_FULL)
    reply = text2cad.Conversation(backend).step("втулка 30 на 20 длиной 50 из нержавейки")
    assert reply.kind == "build"
    assert reply.part["material"]["short"] == "12Х18Н10Т (нержавейка)"
    assert "Материал: 12Х18Н10Т" in reply.text


def test_conversation_asks_for_missing_size_and_material_then_builds():
    backend = _ScriptedBackend(BUSHING_30_20, BUSHING_FULL)
    conversation = text2cad.Conversation(backend)
    reply = conversation.step("втулка 30 на 20")
    assert reply.kind == "ask" and conversation.waiting
    assert "Понял: втулка, наружный диаметр 30, внутренний диаметр 20." in reply.text
    assert "Уточните: длина; материал (например: сталь 45" in reply.text and "по умолчанию — Сталь 45" in reply.text

    reply = conversation.step("длина 50, нержавейка")
    assert reply.kind == "build"
    assert backend.requests[-1] == "втулка 30 на 20. длина 50, нержавейка"  # read again as a whole
    assert reply.part["params"]["length"] == 50
    assert reply.part["material"]["short"] == "12Х18Н10Т (нержавейка)"


def test_conversation_takes_material_from_the_text_when_the_model_misses_it():
    backend = _ScriptedBackend(BUSHING_30_20, dict(BUSHING_30_20, params={"outer_d": 30, "inner_d": 20, "length": 50}))
    conversation = text2cad.Conversation(backend)
    conversation.step("втулка 30 на 20")
    reply = conversation.step("50, бронза")
    assert reply.kind == "build" and reply.part["material"]["short"] == "бронза БрАЖ9-4"


def test_conversation_defaults_answer_builds_without_asking_the_model_again():
    backend = _ScriptedBackend(BUSHING_30_20)
    conversation = text2cad.Conversation(backend)
    conversation.step("втулка 30 на 20")
    reply = conversation.step("по умолчанию")
    assert reply.kind == "build" and len(backend.requests) == 1
    assert reply.part["material"]["short"] == "Сталь 45"
    assert "длина: 40" in reply.text and "материал Сталь 45" in reply.text


def test_conversation_partial_defaults():
    backend = _ScriptedBackend(BUSHING_30_20, dict(BUSHING_30_20, params={"outer_d": 30, "inner_d": 20, "length": 50}))
    conversation = text2cad.Conversation(backend)
    conversation.step("втулка 30 на 20")
    reply = conversation.step("длина 50, материал любой")
    assert reply.kind == "build" and reply.part["material"]["short"] == "Сталь 45"


def test_conversation_cancel():
    conversation = text2cad.Conversation(_ScriptedBackend(BUSHING_30_20))
    conversation.step("втулка 30 на 20")
    assert conversation.step("Отмена").kind == "cancel"


def test_conversation_asks_at_most_twice():
    backend = _ScriptedBackend(BUSHING_30_20)
    conversation = text2cad.Conversation(backend)
    assert conversation.step("втулка 30 на 20").kind == "ask"
    assert conversation.step("ну какая-то").kind == "ask"
    reply = conversation.step("не знаю")
    assert reply.kind == "build" and "по умолчанию" in reply.text


def test_conversation_unknown_material_is_asked_not_guessed():
    backend = _ScriptedBackend(dict(BUSHING_FULL, material="медь"))
    reply = text2cad.Conversation(backend).step("втулка 30 на 20 длиной 50 из меди")
    assert reply.kind == "ask"
    assert "Материал «медь» я не знаю" in reply.text and "длина" not in reply.text.split("Уточните:")[1]


def test_conversation_without_questions():
    reply = text2cad.Conversation(_ScriptedBackend(BUSHING_30_20), ask=False).step("втулка 30 на 20")
    assert reply.kind == "build" and reply.part["material"]["short"] == "Сталь 45"
    reply = text2cad.Conversation(_ScriptedBackend(dict(BUSHING_FULL, material="медь")), ask=False).step("из меди")
    assert "материал Сталь 45 (материал «медь» не знаю)" in reply.text


def test_cli_asks_through_input(monkeypatch, capsys):
    from cad import __main__ as cli

    answers = iter(["длина 50, нержавейка"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    part = cli._ask_until_built("втулка 30 на 20", _ScriptedBackend(BUSHING_30_20, BUSHING_FULL), ask=True)
    assert part["material"]["short"] == "12Х18Н10Т (нержавейка)"
    assert "Уточните" in capsys.readouterr().out


def test_cli_without_console_takes_defaults(monkeypatch):
    from cad import __main__ as cli

    def no_console(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_console)
    part = cli._ask_until_built("втулка 30 на 20", _ScriptedBackend(BUSHING_30_20), ask=True)
    assert part["material"]["short"] == "Сталь 45"


# ── desktop app: "/компас ..." and the menu item ──────────────────────────────

def _cad_window(tmp_path, backend):
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from desktop_browser.app import FluxionWindow

    app = QApplication.instance() or QApplication([])
    window = FluxionWindow(backend=backend, qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))

    def drain():
        deadline = time.time() + 5
        while window.generating and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()

    return window, drain


@pytest.fixture
def fake_kompas(monkeypatch):
    from cad import kompas

    built = []

    def fake_build(part, save_path=None, log=print):
        built.append((part, save_path))
        return kompas.BuildResult(volume=19634.95, mass=154.134, path=save_path)

    monkeypatch.setattr(kompas, "build_part", fake_build)
    return built


def test_chat_command_builds_part_in_background(tmp_path, fake_kompas):
    from cad import kompas

    backend = _ScriptedBackend(dict(BUSHING_FULL, params=dict(BUSHING_FULL["params"], chamfer=1), material="бронза"))
    window, drain = _cad_window(tmp_path, backend)
    window.prompt.setText("/компас втулка 30 на 20 длиной 50, фаска 1, бронза")
    window.send_message()
    window._append_banner("Недоступно: Веб-поиск")  # a banner arrives while the part is built
    drain()

    assert not window.generating and window.cad_worker is None and window._cad_conversation is None
    answer = next(m for m in window._messages if m["role"] == "assistant")
    assert "Шаблон: Втулка" in answer["text"] and "Готово" in answer["text"]
    assert "Материал: бронза БрАЖ9-4" in answer["text"] and "Масса: 154 г." in answer["text"]
    assert "(0 — нет)" not in answer["text"]
    assert window._messages[-1] == {"role": "banner", "text": "Недоступно: Веб-поиск", "meta": ""}
    part, path = fake_kompas[0]
    assert part["ops"][-1]["op"] == "chamfer" and part["material"]["density"] == 7.6
    assert path == kompas.part_path("Втулка")


def test_chat_asks_and_next_message_is_the_answer(tmp_path, fake_kompas):
    backend = _ScriptedBackend(BUSHING_30_20, BUSHING_FULL)
    window, drain = _cad_window(tmp_path, backend)
    window.prompt.setText("/компас втулка 30 на 20")
    window.send_message()
    drain()

    assert not window.generating and not fake_kompas
    assert window._cad_conversation is not None
    assert "Уточните: длина; материал" in window.messages.toPlainText()
    assert "отмена" in window.prompt.placeholderText()

    window.prompt.setText("длина 50, нержавейка")  # no "/компас": it is the answer
    window.send_message()
    drain()

    assert window._cad_conversation is None
    assert backend.requests[-1] == "втулка 30 на 20. длина 50, нержавейка"
    assert fake_kompas[0][0]["material"]["short"] == "12Х18Н10Т (нержавейка)"
    assert "Готово" in window.messages.toPlainText()
    assert window.prompt.placeholderText() == "Напишите сообщение..."


def test_chat_cancel_after_question(tmp_path, fake_kompas):
    window, drain = _cad_window(tmp_path, _ScriptedBackend(BUSHING_30_20))
    window.prompt.setText("/компас втулка 30 на 20")
    window.send_message()
    drain()
    window.prompt.setText("отмена")
    window.send_message()
    drain()
    assert window._cad_conversation is None and not fake_kompas
    assert "Хорошо, деталь не строю." in window.messages.toPlainText()


def test_cad_errors_are_plain_text(tmp_path, monkeypatch):
    from cad import kompas

    def no_kompas(part, save_path=None, log=print):
        raise kompas.KompasError("КОМПАС-3D не найден или не запускается.")

    monkeypatch.setattr(kompas, "build_part", no_kompas)
    window, drain = _cad_window(tmp_path, _ScriptedBackend(BUSHING_FULL))
    window.prompt.setText("/kompas втулка")
    window.send_message()
    drain()
    assert "КОМПАС-3D не найден" in window.messages.toPlainText()
    assert window._cad_conversation is None

    window.prompt.setText("/компас шестерня")
    window.backend = _ScriptedBackend({"template": "gear", "params": {}})
    window.send_message()
    drain()
    text = window.messages.toPlainText()
    assert "Не получилось разобрать описание" in text and "Traceback" not in text


def test_menu_item_takes_text_from_chat_input(tmp_path):
    window, drain = _cad_window(tmp_path, None)
    window.prompt.setText("втулка 30 на 20")
    window._open_cad_dialog()
    drain()
    assert window.prompt.text() == ""
    assert "/компас втулка 30 на 20" in window.messages.toPlainText()
    assert "Движок не подключён" in window.messages.toPlainText()


# ── materials catalog ────────────────────────────────────────────────────────

@pytest.fixture
def user_catalog(tmp_path, monkeypatch):
    """The user's catalog in a temp %APPDATA%; the cached catalog is reloaded around the test."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    materials.load(reload=True)
    yield tmp_path / "Fluxion" / "materials.json"
    monkeypatch.undo()
    materials.load(reload=True)


def test_builtin_catalog_is_valid():
    catalog, errors = materials.read_catalog(materials.BUILTIN_PATH, "builtin")
    assert errors == []
    assert len({m.name for m in catalog}) == len(catalog)
    sheets = [m for m in catalog if m.is_sheet]
    assert sheets and all(m.thicknesses for m in sheets)
    assert all(m.mass_m2 and m.note for m in catalog if m.kind == "checker")


def test_user_catalog_overrides_and_adds(user_catalog):
    user_catalog.parent.mkdir(parents=True)
    user_catalog.write_text(json.dumps({"materials": [
        {"name": "Сталь 45 ГОСТ 1050-2013", "short": "Сталь 45", "density": 7.8, "synonyms": ["сталь 45"]},
        {"name": "Лист 10ХСНД ГОСТ 19281-2014", "short": "лист 10ХСНД", "kind": "sheet", "density": 7.85,
         "synonyms": ["10хснд"], "thicknesses": [4, 5, 6]},
        {"name": "Плохой", "density": "много"},
    ]}, ensure_ascii=False), encoding="utf-8")
    catalog, errors = materials.load_with_errors(reload=True)
    assert len(errors) == 1 and "Плохой" in errors[0]
    steel = [m for m in catalog if m.name == "Сталь 45 ГОСТ 1050-2013"]
    assert len(steel) == 1 and steel[0].density == 7.8 and steel[0].source == "user"
    assert spec.find_material("лист 10ХСНД 5 мм", materials.SHEET_KINDS).short == "лист 10ХСНД"


@pytest.mark.parametrize("text, kinds, short", [
    ("09г2с", materials.SHEET_KINDS, "лист 09Г2С"),
    ("09Г2С", None, "09Г2С"),
    ("рифлёнка 4", materials.SHEET_KINDS, "рифлёный лист Ст3 (чечевица)"),
    ("чечевица 5 мм", materials.SHEET_KINDS, "рифлёный лист Ст3 (чечевица)"),
    ("рифлёный лист 09г2с", materials.SHEET_KINDS, "рифлёный лист 09Г2С (чечевица)"),
    ("оцинкованная сталь", materials.SHEET_KINDS, "оцинковка"),
    ("нержавейка", materials.SHEET_KINDS, "лист 12Х18Н10Т (нержавейка)"),
    ("нержавейка", ("solid",), "12Х18Н10Т (нержавейка)"),
    ("алюминий", materials.SHEET_KINDS, "лист АМг2 (алюминий)"),
    ("сталь 45", materials.SHEET_KINDS, "Сталь 45"),  # no such sheet: the solid grade is the fallback
])
def test_find_material_by_kind(text, kinds, short):
    assert spec.find_material(text, kinds).short == short


def test_checker_plate_mass_from_the_table():
    checker = spec.find_material("рифлёнка", materials.SHEET_KINDS)
    assert checker.density_for(4) == pytest.approx(33.6 / 4)
    assert checker.density_for(3.5) == checker.density  # not in the table: plain steel
    part, _ = spec.build("sheet_angle", {"leg_a": 50, "thickness": 4, "width": 100}, material=checker)
    assert part["material"]["density"] == pytest.approx(8.4)
    text = text2cad.describe(part, [])
    assert "сверьте с ГОСТ 8568-77" in text


def test_sheet_thickness_assortment():
    sheet_09g2s = spec.find_material("09г2с", materials.SHEET_KINDS)
    assert sheet_09g2s.has_thickness(3) and not sheet_09g2s.has_thickness(3.5)
    assert sheet_09g2s.nearest_thicknesses(3.5) == [2, 3, 4]
    assert sheet_09g2s.nearest_thicknesses(7) == [5, 6, 8]


def _csv(path, text):
    path.write_text(text, encoding="utf-8-sig")
    return path


def test_import_csv_lists_bad_rows(user_catalog, tmp_path):
    table = _csv(tmp_path / "materials.csv",
                 "Название;Марка/ГОСТ;Плотность;Толщины;Синонимы;Масса 1 м2\n"
                 "Лист 10ХСНД;ГОСТ 19281-2014;7,85;4 5 6 8;10хснд;\n"
                 "Медь М1;ГОСТ 859-2014;8,94;;медь, м1;\n"
                 "Свинец;;много;;свинец;\n"
                 ";;7,85;;;\n"
                 "Рифлёный 10ХСНД;;7,85;4 5;рифл 10хснд;33,6 41,7\n")
    result = materials.import_table(table)
    assert [m.short for m in result.added] == ["Лист 10ХСНД", "Медь М1", "Рифлёный 10ХСНД"]
    assert len(result.errors) == 2 and result.errors[0].startswith("строка 4") and "строка 5" in result.errors[1]
    copper = spec.find_material("медь")
    assert copper.density == 8.94 and copper.kind == "solid" and copper.name == "Медь М1 ГОСТ 859-2014"
    sheet_10 = spec.find_material("10ХСНД", materials.SHEET_KINDS)
    assert sheet_10.kind == "sheet" and sheet_10.thicknesses == (4, 5, 6, 8)
    assert spec.find_material("рифл 10хснд", materials.SHEET_KINDS).kind == "checker"

    again = materials.import_table(_csv(tmp_path / "again.csv", "название;плотность\nМедь М1 ГОСТ 859-2014;8,9\n"))
    assert [m.short for m in again.replaced] == ["Медь М1 ГОСТ 859-2014"] and not again.added


def test_import_needs_name_and_density_columns(user_catalog, tmp_path):
    result = materials.import_table(_csv(tmp_path / "x.csv", "марка;толщины\nСт3;2\n"))
    assert not result.added and "название" in result.errors[0]


def test_import_excel(user_catalog, tmp_path):
    import zipfile

    def cell(ref, text):
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'

    rows = [["Название", "Плотность", "Толщины", "Синонимы"], ["Лист 10ХСНД", "7.85", "4 5 6", "10хснд"],
            ["Ничего", "", "", ""]]
    sheet_xml = "".join(f'<row r="{i}">' + "".join(cell(f"{chr(65 + j)}{i}", v) for j, v in enumerate(r) if v)
                        + "</row>" for i, r in enumerate(rows, 1))
    path = tmp_path / "materials.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml",
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f"<sheetData>{sheet_xml}</sheetData></worksheet>")
    result = materials.import_table(path)
    assert [m.short for m in result.added] == ["Лист 10ХСНД"] and len(result.errors) == 1
    assert spec.find_material("10хснд", materials.SHEET_KINDS).thicknesses == (4, 5, 6)


# ── sheet metal ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("r, t, k", [(2, 2, 0.32), (1, 2, 0.25), (2.2, 2, 0.325), (20, 2, 0.5), (0.1, 2, 0.21)])
def test_k_factor_table(r, t, k):
    assert sheet.k_factor(r, t) == pytest.approx(k)


def test_angle_flat_length_matches_kompas():
    # KOMPAS v24 unfolded this angle (outer legs 50 and 30, s2, R2, K 0.4) to 76.398 mm
    profile = sheet.build_profile([50, 30], [90], 2, 2, 0.4, start_direction=-90)
    assert profile.flat_length == pytest.approx(76.398, abs=1e-3)
    assert profile.straights == pytest.approx([46, 26])
    inside = sheet.build_profile([48, 28], [90], 2, 2, 0.4, inside=True, start_direction=-90)
    assert inside.flat_length == pytest.approx(profile.flat_length)
    assert inside.points == profile.points


@pytest.mark.parametrize("key, params, flat", [
    ("sheet_channel", {"back": 100, "flange": 40, "thickness": 1.5, "radius": 2, "k": 0.4, "width": 60},
     (100 - 2 * 3.5) + 2 * (40 - 3.5) + 2 * math.pi / 2 * (2 + 0.6)),
    ("sheet_z", {"flange": 30, "web": 60, "thickness": 2, "radius": 2, "k": 0.4, "width": 200},
     (30 - 4) + (60 - 8) + (30 - 4) + 2 * math.pi / 2 * 2.8),
    ("sheet_angle", {"leg_a": 50, "leg_b": 40, "angle": 120, "thickness": 2, "radius": 2, "k": 0.4, "width": 80},
     50 + 40 - 2 * 4 * math.tan(math.radians(30)) + math.pi / 3 * 2.8),
    ("sheet_shelf", {"length": 400, "depth": 200, "flange": 25, "flanges": 2, "thickness": 1.5, "k": 0.4},
     (200 - 6) + 2 * (25 - 3) + 2 * math.pi / 2 * (1.5 + 0.6)),
])
def test_flat_length_by_formula(key, params, flat):
    part, _ = spec.build(key, params)
    assert part["sheet"]["flat_length"] == pytest.approx(flat, abs=1e-3)


def test_sheet_profiles_have_outer_sizes():
    part, _ = spec.build("sheet_z", {"flange": 30, "web": 60, "flange2": 20, "thickness": 2, "width": 100})
    points = part["ops"][0]["profile"]
    # sketch line on the upper/right face: top at Z 60, bottom flange face at Z = t, flanges 30 and 20 outside
    assert points[0][1] == 60 and points[-1][1] == 2
    assert points[1][0] - points[0][0] == 30 and points[-1][0] - (points[2][0] - 2) == 20
    channel, _ = spec.build("sheet_channel", {"back": 100, "flange": 40, "thickness": 2, "width": 50})
    xs = [x for x, _ in channel["ops"][0]["profile"]]
    assert min(xs) == 2 and max(xs) == 98  # inner faces; outer 0..100


def test_sheet_part_reports_bends_and_side():
    part, _ = spec.build("sheet_channel", {"back": 100, "flange": 40, "thickness": 2, "width": 50, "inside": 1})
    assert part["sheet"]["bends"] == 2 and part["sheet"]["inside"]
    text = text2cad.describe(part, [])
    assert "по внутренней стороне" in text and "K = 0.32 по таблице" in text and "сгибов: 2" in text
    outer, _ = spec.build("sheet_angle", {"leg_a": 40, "thickness": 2, "width": 50})
    assert "по наружной стороне" in text2cad.describe(outer, [])


def test_sheet_holes_in_flanges():
    part, _ = spec.build("sheet_bracket", {"leg_a": 60, "leg_b": 40, "thickness": 3, "width": 50})
    cuts = [op for op in part["ops"] if op["op"] == "sheet_cut"]
    assert [c["plane"] for c in cuts] == ["YOZ", "XOY"]
    vertical, horizontal = cuts
    assert vertical["offset"] == 3 and [s["circle"]["cx"] for s in vertical["shapes"]] == [12.5, 37.5]
    # in the middle of the flat part: from the bend end (r + t = 6) to the free edge (60)
    assert vertical["shapes"][0]["circle"]["cy"] == pytest.approx((6 + 60) / 2)
    assert horizontal["shapes"][0]["circle"]["cx"] == pytest.approx((6 + 40) / 2)


@pytest.mark.parametrize("key, params, message", [
    ("sheet_angle", {"leg_a": 50, "thickness": 2, "radius": 0.1, "width": 50}, "Радиус сгиба слишком мал"),
    ("sheet_angle", {"leg_a": 5, "thickness": 2, "width": 50}, "Полка 1 слишком короткая"),
    ("sheet_angle", {"leg_a": 50, "thickness": 40, "width": 50}, "Толщина листа"),
    ("sheet_angle", {"leg_a": 50, "thickness": 2, "width": 50, "k": 0.7}, "Коэффициент K"),
    ("sheet_angle", {"leg_a": 50, "angle": 120, "thickness": 2, "width": 50, "hole_d": 6, "holes_a": 2},
     "сгибах на 90°"),
    ("sheet_bracket", {"leg_a": 20, "thickness": 3, "width": 50, "hole_d": 9}, "не помещаются"),
    ("sheet_channel", {"back": 100, "flange": 40, "thickness": 2, "width": 20, "hole_d": 9, "holes_back": 3},
     "не помещаются по ширине"),
    ("sheet_tray", {"length": 200, "width_b": 150, "height": 30, "sides": 3, "thickness": 1}, "2 или 4"),
])
def test_sheet_validation(key, params, message):
    with pytest.raises(spec.SpecError, match=message):
        spec.build(key, params)


def test_box_walls_do_not_meet_at_the_corners():
    part, _ = spec.build("sheet_tray", {"length": 200, "width_b": 150, "height": 30, "sides": 4, "thickness": 1})
    box = part["ops"][0]
    assert box["op"] == "sheet_box" and box["bends"] == 4
    assert box["corner_gap"] > box["thickness"]  # a wall stops short of the neighbour's bend zone


def test_conversation_asks_for_a_sheet_thickness_out_of_the_assortment():
    backend = _ScriptedBackend(
        {"template": "sheet_angle", "params": {"leg_a": 50, "leg_b": 30, "thickness": 3.5, "width": 100},
         "material": "09г2с"},
    )
    conversation = text2cad.Conversation(backend)
    reply = conversation.step("уголок 50х30 из листа 3.5, длина 100, 09г2с")
    assert reply.kind == "ask"
    assert "толщина — 3.5 мм в сортаменте «лист 09Г2С» нет, ближайшие: 2, 3, 4 мм" in reply.text
    reply = conversation.step("по умолчанию")
    assert reply.kind == "build" and reply.part["params"]["thickness"] == 3
    assert "толщина 3 мм — ближайшая к 3.5" in reply.text


def test_conversation_sheet_material_question_offers_sheets():
    backend = _ScriptedBackend({"template": "sheet_angle", "params": {"leg_a": 50, "thickness": 2, "width": 100}})
    reply = text2cad.Conversation(backend).step("уголок 50 из листа 2, длина 100")
    assert reply.kind == "ask" and "по умолчанию — лист Ст3" in reply.text and "рифлёный" in reply.text


def test_result_text_lists_flat_pattern_and_dxf(tmp_path):
    from cad.kompas import BuildResult

    part, _ = spec.build("sheet_angle", {"leg_a": 50, "thickness": 2, "width": 100})
    result = BuildResult(volume=1, mass=1534, path=tmp_path / "Уголок.m3d", flat_size=(96.15, 100, 2),
                         dxf_path=tmp_path / "Уголок — развёртка.dxf", dxf_bends=1, dxf_holes=0)
    text = text2cad.result_text(part, result)
    assert "Масса: 1.53 кг." in text and "Развёртка (КОМПАС): 96.15 × 100.00 мм, толщина 2 мм." in text
    assert "DXF развёртки:" in text and "развёртка.dxf" in text


# ── DXF ──────────────────────────────────────────────────────────────────────

KOMPAS_DXF = """0
SECTION
2
ENTITIES
0
INSERT
8
0
2
T0
10
0.0
20
0.0
0
LINE
8
Системный слой
6
K5LT_BASIC
10
130.0
20
150.0
11
130.0
21
210.0
0
LINE
6
K5LT_BASIC
10
49.6
20
150.0
11
97.6
21
150.0
0
LINE
6
K5LT_BASIC
10
97.6
20
150.0
11
130.0
21
150.0
0
LINE
6
K5LT_BASIC
10
49.6
20
210.0
11
130.0
21
210.0
0
LINE
6
K5LT_BASIC
10
49.6
20
150.0
11
49.6
21
210.0
0
LINE
6
K5LT_CHAIN_LINE_WITH_2_POINTS
10
99.8
20
150.0
11
99.8
21
210.0
0
CIRCLE
6
K5LT_BASIC
10
70.0
20
180.0
40
4.5
0
ENDSEC
0
EOF
"""


def test_dxf_cleaned_for_laser(tmp_path):
    from cad import dxf

    source = tmp_path / "kompas.dxf"
    source.write_text(KOMPAS_DXF, encoding="utf-8")
    pattern = dxf.convert(source, tmp_path / "clean.dxf")
    assert pattern.size == pytest.approx((80.4, 60.0))
    assert pattern.bend_lines == 1 and pattern.holes == 1
    assert len(pattern.of(dxf.CUT, "LINE")) == 4  # the two lower pieces joined
    again = dxf.read(tmp_path / "clean.dxf")
    assert again.size == pytest.approx((80.4, 60.0)) and again.bend_lines == 1 and again.holes == 1
    text = (tmp_path / "clean.dxf").read_text(encoding="ascii")
    assert "AC1009" in text and "INSERT" not in text and "\nBEND\n" in text
    assert again.of(dxf.CUT, "CIRCLE")[0].data == pytest.approx((20.4, 30.0, 4.5))


def test_every_prompt_example_is_valid_json_that_builds():
    examples = [line.split(" -> ", 1)[1] for line in text2cad.system_prompt().splitlines() if " -> {" in line]
    assert len(examples) >= 20
    for example in examples:
        data = json.loads(example)
        spec.build(data["template"], data["params"])
        if data.get("material"):
            kinds = materials.SHEET_KINDS if spec.TEMPLATES[data["template"]].sheet else ("solid",)
            assert spec.find_material(data["material"], kinds) is not None, data["material"]