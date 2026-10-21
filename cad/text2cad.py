"""Text description -> template, sizes and material, with the chat model.

The model never writes geometry or COM code: it fills a small JSON object
{"template", "name", "params", "material"} (constrained by a JSON schema where
the backend supports it), and ``spec.build`` turns it into checked operations.

``Conversation`` adds the questions: when the main sizes or the material are
not in the description, it asks once (twice at most) before building.  The
chat of the desktop app and the CLI both drive it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import materials, spec

SCHEMA = {
    "type": "object",
    "properties": {
        "template": {"type": "string", "enum": list(spec.TEMPLATES)},
        "name": {"type": "string"},
        "params": {"type": "object"},
        "material": {"type": "string"},
    },
    "required": ["template", "params"],
}

MAX_QUESTIONS = 2


def system_prompt() -> str:
    lines = [
        "Ты переводишь описание детали в JSON для построения в САПР.",
        "Выбери один шаблон и перенеси размеры из описания в миллиметрах.",
        "Не выдумывай размеры: чего нет в описании, не указывай — Fluxion переспросит или возьмёт значение по умолчанию.",
        'Ответ — только JSON: {"template": "...", "name": "короткое название", "params": {...}, "material": "..."}.',
        "material — материал, только если он назван в описании, словами из описания "
        "(«сталь 45», «нержавейка», «Д16Т», «из бронзы» -> «бронза»); если не назван — не указывай.",
        "Фаска (chamfer) — размер катета в мм; «фаска 1×45°» или «фаски 1» -> 1.",
        "Отверстия «в торцах», «с боков» и «поперечные» — отдельные параметры, не путай их с отверстиями в плане.",
        "Детали из листа со сгибами — шаблоны sheet_*: «из листа 2 мм», «лист 1.5», «s2» -> thickness; "
        "«сгибы R2», «радиус гиба 3» -> radius; «уголок 50х30» -> leg_a 50, leg_b 30; размеры полок по наружной "
        "стороне, а если сказано «по внутреннему» — inside 1; «короб»/«коробка» — 4 борта (sides 4), «лоток» — 2.",
        "Материалы (называй как в описании): " + ", ".join(dict.fromkeys(m.short for m in materials.load())) + ".",
        "",
        "Шаблоны:",
    ]
    for t in spec.TEMPLATES.values():
        params = ", ".join(f"{p.name} — {p.title}" for p in t.params)
        lines.append(f"- {t.key}: {t.hint}. Параметры: {params}.")
        text, example = t.example
        if text:
            lines.append(f'  Пример: «{text}» -> {json.dumps({"template": t.key, "params": example}, ensure_ascii=False)}')
    lines += [
        "",
        "Ещё примеры:",
        '«втулка 30 на 20 длиной 50 из бронзы» -> {"template": "bushing", "params": {"outer_d": 30, "inner_d": 20, '
        '"length": 50}, "material": "бронза"}',
        '«втулка 30 на 20» -> {"template": "bushing", "params": {"outer_d": 30, "inner_d": 20}}',
        '«фланец 200 толщиной 20, сталь 20» -> {"template": "flange", "params": {"outer_d": 200, "thickness": 20}, '
        '"material": "сталь 20"}',
        '«уголок 50х30 из листа 2 мм» -> {"template": "sheet_angle", "params": {"leg_a": 50, "leg_b": 30, "thickness": 2}}',
        '«короб 200х150, борт 30, лист 1 мм, оцинковка» -> {"template": "sheet_tray", "params": {"length": 200, '
        '"width_b": 150, "height": 30, "sides": 4, "thickness": 1}, "material": "оцинковка"}',
        '«лоток из рифлёнки 4, дно 300 на 120, борта 40 по внутреннему» -> {"template": "sheet_tray", "params": '
        '{"length": 300, "width_b": 120, "height": 40, "sides": 2, "thickness": 4, "inside": 1}, "material": "рифлёнка"}',
        '«Г-образный кронштейн 80 на 60 из нержавейки 2 мм шириной 40, по два отверстия 8 в каждой полке» -> '
        '{"template": "sheet_bracket", "params": {"leg_a": 80, "leg_b": 60, "thickness": 2, "width": 40, "hole_d": 8, '
        '"holes_a": 2, "holes_b": 2}, "material": "нержавейка"}',
        '«швеллер гнутый 100х50х3 длиной 1000» -> {"template": "sheet_channel", "params": {"back": 100, "flange": 50, '
        '"thickness": 3, "width": 1000}}',
        '«зетка 40-80-40, s2, длина 500, 09Г2С» -> {"template": "sheet_z", "params": {"flange": 40, "web": 80, '
        '"flange2": 40, "thickness": 2, "width": 500}, "material": "09Г2С"}',
        '«полка 600х250 с отбортовкой 30 спереди, лист 1.2» -> {"template": "sheet_shelf", "params": {"length": 600, '
        '"depth": 250, "flange": 30, "flanges": 1, "thickness": 1.2}}',
        '«короб 300 на 200 высотой 50 по внутреннему, оцинковка 0.8» -> {"template": "sheet_tray", "params": '
        '{"length": 300, "width_b": 200, "height": 50, "sides": 4, "thickness": 0.8, "inside": 1}, "material": "оцинковка"}',
        '«уголок с углом 120 градусов, полки 50 и 40, лист 2, длина 80» -> {"template": "sheet_angle", "params": '
        '{"leg_a": 50, "leg_b": 40, "angle": 120, "thickness": 2, "width": 80}}',
    ]
    return "\n".join(lines)


def _value(param: spec.Param, value) -> str:
    if param.kind == "steps":
        return ", ".join(f"⌀{float(s['d']):g}×{float(s['length']):g}" for s in value)
    return f"{float(value):g}"


def describe(part: dict, assumed: list[str]) -> str:
    """What was understood: template, sizes, material and the defaults taken (for the user)."""
    tpl = spec.TEMPLATES[part["template"]]
    sizes = [f"{spec.plain_title(p)}: {_value(p, part['params'][p.name])}"
             for p in tpl.params if part["params"].get(p.name)]
    text = f"Шаблон: {tpl.title}. " + "; ".join(sizes) + "."
    sheet = part.get("sheet")
    if sheet:
        k_note = " по таблице для r/t" if not part["params"].get("k") else ""
        side = "внутренней" if sheet["inside"] else "наружной"
        text += (f"\nЛист {sheet['thickness']:g} мм, внутренний радиус сгиба R{sheet['radius']:g}, "
                 f"K = {sheet['k']:g}{k_note}; размеры полок — по {side} стороне."
                 f"\nРазвёртка по расчёту: {sheet['flat_length']:.1f} × {sheet['flat_width']:.1f} мм, "
                 f"сгибов: {sheet['bends']}.")
    material = part.get("material")
    if material:
        text += f"\nМатериал: {material['short']} ({material['density']:g} г/см³)."
        if material.get("note"):
            text += f"\n⚠ {material['note']}."
    if assumed:
        text += "\nНе было в описании, взято по умолчанию: " + "; ".join(assumed) + "."
    return text


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise spec.SpecError("Модель не вернула JSON.") from None


@dataclass
class Draft:
    """What the model read out of the description, before any defaults."""

    template: str
    name: str = ""
    params: dict = field(default_factory=dict)
    material_text: str = ""
    source: str = "model"  # "rules": read by cad.quick without the model


def ask_model(description: str, backend) -> Draft:
    """One model call: description -> Draft (template checked, sizes not yet)."""
    from core.config import GenerationSettings

    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": description},
    ]
    gen = GenerationSettings(temperature=0.0, max_tokens=512, json_schema=SCHEMA)
    try:
        raw = backend.generate(messages, gen)
    except TypeError:
        raw = backend.generate(messages)
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise spec.SpecError("Модель вернула не объект JSON.")
    template = str(data.get("template", ""))
    if template not in spec.TEMPLATES:
        raise spec.SpecError(f"Неизвестный тип детали: {template!r}.")
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    return Draft(template, str(data.get("name") or ""), params, str(data.get("material") or "").strip())


def read_description(description: str, backend) -> Draft:
    """Rules first (cad.quick: instant), the model when they are not sure."""
    from . import quick

    draft = quick.read(description)
    if draft is not None:
        draft.source = "rules"
        return draft
    return ask_model(description, backend)


def _material_kinds(template: str) -> tuple[str, ...]:
    return materials.SHEET_KINDS if spec.TEMPLATES[template].sheet else ("solid",)


def _find_material(draft: "Draft", description: str):
    kinds = _material_kinds(draft.template)
    return spec.find_material(draft.material_text, kinds) or spec.find_material(description, kinds)


def parse(description: str, backend) -> tuple[dict, list[str]]:
    """Ask the model and build at once (no questions); return (operation spec, assumptions)."""
    draft = read_description(description, backend)
    return spec.build(draft.template, draft.params, draft.name, _find_material(draft, description))


def _thickness(draft: "Draft") -> float | None:
    try:
        value = float(str(draft.params.get("thickness")).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# ── questions ────────────────────────────────────────────────────────────────

_CANCEL = re.compile(r"^\W*(отмен\w*|стоп|не\s+надо|cancel)\W*$", re.IGNORECASE)
_DEFAULT_WORDS = r"(по\s+умолчанию|любо\w+|неважно|не\s+важно|как\s+угодно|на\s+твой\s+выбор|default)"
_ONLY_DEFAULTS = re.compile(rf"^\W*(бери\s+|возьми\s+|пусть\s+)?{_DEFAULT_WORDS}\W*$", re.IGNORECASE)
_HAS_DEFAULTS = re.compile(_DEFAULT_WORDS, re.IGNORECASE)


@dataclass
class Reply:
    kind: str  # "ask": a question; "build": part is ready; "cancel"
    text: str
    part: dict | None = None


class Conversation:
    """Description -> (questions) -> part.  Feed the user's messages to ``step``.

    The first message is the description; after a question the next message
    is the answer: it is added to the description and the model reads it all
    again.  "отмена" cancels, "по умолчанию"/"любой" takes the defaults, and
    after MAX_QUESTIONS questions the part is built with defaults anyway.
    For a sheet part a thickness the chosen sheet is not rolled in is asked
    about too (the nearest ones offered); with defaults the nearest is taken.
    """

    def __init__(self, backend, ask: bool = True) -> None:
        self.backend = backend
        self.ask = ask
        self.description = ""
        self.questions = 0
        self.draft: Draft | None = None

    @property
    def waiting(self) -> bool:
        """A question was asked and the answer is expected."""
        return self.questions > 0

    def step(self, message: str) -> Reply:
        message = message.strip()
        if self.waiting and _CANCEL.match(message):
            return Reply("cancel", "Хорошо, деталь не строю.")
        only_defaults = self.draft is not None and bool(_ONLY_DEFAULTS.match(message))
        if not only_defaults:
            self.description = f"{self.description}. {message}" if self.description else message
            self.draft = read_description(self.description, self.backend)
        draft = self.draft
        material = _find_material(draft, self.description)
        missing = spec.missing_params(draft.template, draft.params)
        thickness = _thickness(draft)
        off_gauge = (material is not None and material.is_sheet and thickness is not None
                     and not material.has_thickness(thickness))
        take_defaults = only_defaults or (self.waiting and bool(_HAS_DEFAULTS.search(message)))
        if ((missing or material is None or off_gauge) and self.ask and not take_defaults
                and self.questions < MAX_QUESTIONS):
            self.questions += 1
            unknown = draft.material_text if material is None and draft.material_text else ""
            gauge = (material, thickness) if off_gauge else None
            return Reply("ask", question(draft, missing, material is None, unknown, gauge))
        notes = []
        kind = "sheet" if spec.TEMPLATES[draft.template].sheet else "solid"
        if material is None:
            material = materials.default(kind)
            unknown = f" (материал «{draft.material_text}» не знаю)" if draft.material_text else ""
            notes.append(f"материал {material.short}{unknown}")
        params = dict(draft.params)
        if material.is_sheet and thickness is not None and not material.has_thickness(thickness):
            nearest = material.nearest_thicknesses(thickness, 1)[0]
            params["thickness"] = nearest
            notes.append(f"толщина {nearest:g} мм — ближайшая к {thickness:g} в сортаменте «{material.short}»")
        part, assumed = spec.build(draft.template, params, draft.name, material)
        return Reply("build", describe(part, assumed + notes), part)


def question(draft: Draft, missing: list[str], need_material: bool, unknown_material: str = "",
             gauge: tuple | None = None) -> str:
    """One short question with everything that is missing."""
    tpl = spec.TEMPLATES[draft.template]
    known = []
    for p in tpl.params:
        value = draft.params.get(p.name)
        if value in (None, "", []):
            continue
        try:
            known.append(f"{spec.plain_title(p)} {_value(p, value)}")
        except (TypeError, ValueError, KeyError):
            known.append(f"{spec.plain_title(p)} {value}")
    lines = [f"Понял: {tpl.title.lower()}" + (", " + ", ".join(known) if known else "") + "."]
    asks = list(missing)
    if gauge is not None:
        material, thickness = gauge
        nearest = ", ".join(f"{t:g}" for t in material.nearest_thicknesses(thickness))
        asks.append(f"толщина — {thickness:g} мм в сортаменте «{material.short}» нет, ближайшие: {nearest} мм")
    if need_material:
        kind = "sheet" if tpl.sheet else "solid"
        common = ", ".join(materials.common(kind))
        if unknown_material:
            lines.append(f"Материал «{unknown_material}» я не знаю — плотность выдумывать не буду.")
        asks.append(f"материал (например: {common}; по умолчанию — {materials.default(kind).short})")
    lines.append("Уточните: " + "; ".join(asks) + ".")
    lines.append("Ответьте одним сообщением. «по умолчанию» — взять стандартные значения, «отмена» — не строить.")
    return "\n".join(lines)


def result_text(part: dict, result) -> str:
    """The final lines of the answer: where the part is, its mass, the flat pattern and its DXF."""
    lines = [f"Готово: деталь «{part['name']}» сохранена в {result.path}"]
    mass = result.mass_text()
    if mass:
        lines.append(f"Масса: {mass}.")
    if result.flat_size:
        length, width, thickness = result.flat_size
        lines.append(f"Развёртка (КОМПАС): {length:.2f} × {width:.2f} мм, толщина {thickness:g} мм.")
    if result.dxf_path:
        lines.append(f"DXF развёртки: {result.dxf_path}")
    return "\n".join(lines)
