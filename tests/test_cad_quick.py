"""Reading typical part descriptions without the chat model (cad/quick.py)."""
from __future__ import annotations

import json
import re

import pytest

from cad import quick, spec, text2cad


def _known_answers() -> list[tuple[str, str, dict]]:
    """The examples the model is taught with: description -> template and sizes."""
    cases = [(t.example[0], t.key, t.example[1]) for t in spec.TEMPLATES.values() if t.example[0]]
    for line in text2cad.system_prompt().splitlines():
        m = re.match(r"^«(.*)» -> (\{.*\})$", line.strip())
        if m:
            data = json.loads(m.group(2))
            cases.append((m.group(1), data["template"], data["params"]))
    return cases


KNOWN = _known_answers()


@pytest.mark.parametrize("text,template,params", KNOWN, ids=[c[0][:40] for c in KNOWN])
def test_rules_agree_with_the_model_examples_or_pass(text, template, params):
    """Where the rules answer, they give exactly what the model is taught to give."""
    draft = quick.read(text)
    if draft is None:
        return
    expected = dict(params)
    if template == "sheet_tray":
        expected.setdefault("sides", 2)
    assert (draft.template, draft.params) == (template, expected)


def test_rules_read_most_of_the_examples():
    read = [text for text, _, _ in KNOWN if quick.read(text) is not None]
    assert len(read) >= len(KNOWN) - 2, sorted(set(t for t, _, _ in KNOWN) - set(read))


@pytest.mark.parametrize("text,template,params,material", [
    ("Построй втулку 30 на 20 длиной 50 мм из бронзы.", "bushing",
     {"outer_d": 30, "inner_d": 20, "length": 50}, "бронза БрАЖ9-4"),
    ("уголок гнутый из листа 2 мм, полки 60 и 40 мм, ширина 100, сталь 09Г2С", "sheet_angle",
     {"leg_a": 60, "leg_b": 40, "thickness": 2, "width": 100}, "лист 09Г2С"),
    ("кольцо 50 на 40 высотой 10, капролон", "bushing", {"outer_d": 50, "inner_d": 40, "length": 10}, "капролон"),
    ("пластина 200 на 100 толщиной 5 из АМг6", "plate", {"length": 200, "width": 100, "thickness": 5}, "АМг6"),
    ("фланец 150 толщиной 12, 4 отверстия 10 на окружности 110, Ст3", "flange",
     {"outer_d": 150, "thickness": 12, "bolt_count": 4, "bolt_d": 10, "bolt_circle_d": 110}, "Ст3"),
    ("гайка М8 под ключ 13", "nut", {"wrench_size": 13, "hole_d": 8}, ""),
    ("ось 20 длиной 150 из стали 45", "shaft", {"steps": [{"d": 20, "length": 150}]}, "Сталь 45"),
    ("втулка 30,5 x 20 длиной 40", "bushing", {"outer_d": 30.5, "inner_d": 20, "length": 40}, ""),
])
def test_rules_read_typical_phrasings(text, template, params, material):
    draft = quick.read(text)
    assert draft is not None
    assert (draft.template, draft.params, draft.material_text) == (template, params, material)


@pytest.mark.parametrize("text", [
    "втулка 30 на 20 длиной 50 с пазом 5",  # a feature the rules do not know: its number is left
    "пластина 100х50х10 с 2 отверстиями 8",  # holes by the corners are always 4
    "уголок 60х40",  # bent from a sheet or a solid angle?
    "вал 30 и 20",
    "фланец Ду50",
    "втулка и пластина 30 на 20",  # two parts
    "сделай что-нибудь красивое",
    "Г-образный кронштейн 80 на 60 из нержавейки 2 мм шириной 40",  # sheet or not: not said
])
def test_rules_leave_unclear_descriptions_to_the_model(text):
    assert quick.read(text) is None


def test_unknown_material_word_is_kept_for_the_question():
    assert quick.read("втулка из титана 30 на 20 длиной 40").material_text == "титана"


class _NoModel:
    def generate(self, messages, gen=None):
        raise AssertionError("the model was asked")


def test_conversation_without_the_model():
    """A typical part: question and answer, then the part — no model call at all."""
    conversation = text2cad.Conversation(_NoModel())
    reply = conversation.step("втулка 30 на 20")
    assert reply.kind == "ask" and "Уточните: длина; материал" in reply.text
    reply = conversation.step("длина 50, нержавейка")
    assert reply.kind == "build"
    assert reply.part["params"]["length"] == 50
    assert reply.part["material"]["short"] == "12Х18Н10Т (нержавейка)"
    assert conversation.draft.source == "rules"


def test_unknown_material_is_asked_without_the_model():
    reply = text2cad.Conversation(_NoModel()).step("втулка 30 на 20 длиной 50 из титана")
    assert reply.kind == "ask" and "Материал «титана» я не знаю" in reply.text


def test_model_reads_what_the_rules_pass():
    class Model:
        def __init__(self):
            self.calls = 0

        def generate(self, messages, gen=None):
            self.calls += 1
            return json.dumps({"template": "bushing", "params": {"outer_d": 30, "inner_d": 20, "length": 50}})

    model = Model()
    draft = text2cad.read_description("втулка 30 на 20 длиной 50 с пазом 5", model)
    assert model.calls == 1 and draft.source == "model"
