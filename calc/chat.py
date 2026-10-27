"""«/расчёт …» in the chat: the model reads the question into raw values, the formulas do the rest.

The model fills a small JSON object {"calc": kind, "params": {field: value}}
(constrained by a JSON schema where the backend supports it).  The values are
copied from the question as they are, with their units ("5 тс", "1,5 м"):
the unit conversion and every number of the result are made by the package.
When needed values are missing, Fluxion asks once or twice; "отмена"
cancels.  After the calculation the model may put the verdict into two or
three plain sentences — under the numbers, never instead of them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import CALCS, module
from .core import CalcError, Result
from .fields import BOOL, CHOICE, read

SCHEMA = {
    "type": "object",
    "properties": {
        "calc": {"type": "string", "enum": list(CALCS)},
        "params": {"type": "object"},
    },
    "required": ["calc", "params"],
}
MAX_QUESTIONS = 2
WORDS = {
    "weld": "сварной шов: угловой (катет) или стыковой, срез, изгиб",
    "bolt": "болты и резьбовые соединения: срез, смятие, отрыв, затяжка",
    "beam": "балка, лонжерон или рама прицепа на опорах (осях): изгиб, прогиб, подбор профиля",
    "bend": "гибка листа: минимальный радиус, развёртка, K-фактор, усилие пресса",
    "shaft": "вал или ось: кручение и изгиб, выносливость, шпонка",
}

_CANCEL = re.compile(r"^\W*(отмен\w*|стоп|не\s+надо|cancel)\W*$", re.IGNORECASE)


def system_prompt() -> str:
    lines = [
        "Ты разбираешь вопрос инженера в параметры расчёта. Сам ничего не считай и не переводи единицы.",
        "Ответь только JSON: {\"calc\": вид расчёта, \"params\": {имя: значение}}.",
        "Значения пиши строкой так, как в вопросе, вместе с единицами: \"5 тс\", \"200 мм\", \"1,5 м\", \"300 Н·м\".",
        "Параметр, которого нет в вопросе, не пиши. Для выбора из списка пиши один из вариантов.",
        "",
    ]
    for kind, (title, mod) in CALCS.items():
        lines.append(f"calc = \"{kind}\" — {WORDS[kind]}. Параметры:")
        for f in mod.FIELDS:
            if f.advanced:
                continue
            need = " (обязательно)" if f.required else ""
            extra = f"; варианты: {', '.join(f.choices)}" if f.kind == CHOICE else ""
            extra += "; да или нет" if f.kind == BOOL else ""
            hint = f" — {f.hint}" if f.hint else ""
            lines.append(f"  {f.name}: {f.title}{need}{hint}{extra}")
        lines.append("")
    lines += [
        "Примеры:",
        "«угловой шов катет 6 длиной 200 держит 5 тонн, 09Г2С» -> "
        "{\"calc\": \"weld\", \"params\": {\"joint\": \"угловой\", \"leg\": \"6 мм\", \"length\": \"200 мм\", "
        "\"force\": \"5 тс\", \"material\": \"09Г2С\"}}",
        "«лонжерон швеллер 16 длиной 6 м на осях в 1,5 и 4,5 м, груз 3 т по всей длине на две балки, kд 1,5» -> "
        "{\"calc\": \"beam\", \"params\": {\"length\": \"6 м\", \"supports\": \"1,5 м; 4,5 м\", "
        "\"loads\": \"3 тс/6 м\", \"profile\": \"швеллер 16\", \"beams\": \"2\", \"kd\": \"1,5\"}}",
        "Распределённую нагрузку, заданную общим весом, пиши как «вес/длина», например \"3 тс/6 м\".",
    ]
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise CalcError("модель не вернула параметры расчёта")


_TOTAL_LOAD = re.compile(r"^\s*([\d.,]+\s*[^\d\s/]+)\s*/\s*([\d.,]+\s*(?:мм|см|м))\s*$")


def _total_loads(params: dict) -> dict:
    """«3 тс/6 м» (a total weight spread along a length) -> «0,5 тс/м»: the division is ours, not the model's."""
    from . import units

    loads = params.get("loads")
    if not isinstance(loads, str):
        return params
    items = []
    for item in re.split(r";", loads):
        match = _TOTAL_LOAD.match(item)
        if match and units.kind_of(match.group(1)) == units.FORCE:
            q = units.parse(match.group(1), units.FORCE) / units.parse(match.group(2), units.LENGTH)
            items.append(f"{q:.10g} Н/мм" + item[match.end():])
        else:
            items.append(item)
    return {**params, "loads": ";".join(items)}


def read_question(question: str, backend) -> tuple[str, dict]:
    """One model call: the question -> (kind, raw params)."""
    from core.config import GenerationSettings

    messages = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": question}]
    gen = GenerationSettings(temperature=0.0, max_tokens=600, json_schema=SCHEMA)
    try:
        raw = backend.generate(messages, gen)
    except TypeError:
        raw = backend.generate(messages)
    data = _extract_json(raw)
    kind = str(data.get("calc") or "")
    if kind not in CALCS:
        raise CalcError(f"не понял, что считать: {kind!r}")
    names = {f.name for f in module(kind).FIELDS}
    params = {k: v for k, v in (data.get("params") or {}).items()
              if k in names and v not in (None, "") and not isinstance(v, (dict, list))}
    return kind, _total_loads(params)


def explain(result: Result, backend) -> str:
    """Two or three plain sentences on the verdict; "" when the model cannot."""
    from core.config import GenerationSettings

    messages = [
        {"role": "system", "content": "Ты инженер-конструктор. Объясни вывод расчёта коллеге в 2–3 коротких "
                                      "предложениях простыми словами: что держит, что нет и что лучше сделать. "
                                      "Ничего не пересчитывай, бери числа только из расчёта, новых чисел не пиши."},
        {"role": "user", "content": result.text()},
    ]
    try:
        return (backend.generate(messages, GenerationSettings(temperature=0.2, max_tokens=220)) or "").strip()
    except Exception:
        return ""


@dataclass
class Reply:
    kind: str                  # "ask", "result", "cancel", "error"
    text: str
    result: Result | None = None
    calc: str = ""
    params: dict | None = None


class Conversation:
    """Question -> (questions about what is missing) -> result.  Feed the messages to ``step``."""

    def __init__(self, backend) -> None:
        self.backend = backend
        self.question = ""
        self.questions = 0

    @property
    def waiting(self) -> bool:
        return self.questions > 0

    def step(self, message: str) -> Reply:
        message = message.strip()
        if self.waiting and _CANCEL.match(message):
            return Reply("cancel", "Хорошо, не считаю.")
        self.question = f"{self.question}. {message}" if self.question else message
        kind, params = read_question(self.question, self.backend)
        mod = module(kind)
        try:
            missing = read(mod.FIELDS, params, partial=True).missing
        except CalcError as exc:
            return self._ask(kind, f"Не понял значение — {exc}.")
        if missing:
            if self.questions >= MAX_QUESTIONS:
                return Reply("error", "Не хватает данных: " + ", ".join(f.title.lower() for f in missing) + ".",
                             calc=kind, params=params)
            asks = "; ".join(f"{f.title.lower()}" + (f" ({f.hint})" if f.hint else "") for f in missing)
            return self._ask(kind, f"Для расчёта «{CALCS[kind][0].lower()}» уточните: {asks}.")
        try:
            result = mod.calculate(params)
        except CalcError as exc:
            if self.questions < MAX_QUESTIONS:
                return self._ask(kind, f"Не получается посчитать: {exc}. Поправьте данные.")
            return Reply("error", f"Не получается посчитать: {exc}.", calc=kind, params=params)
        return Reply("result", result.text(), result, kind, params)

    def _ask(self, kind: str, text: str) -> Reply:
        self.questions += 1
        return Reply("ask", text + "\nОтветьте одним сообщением или «отмена».", calc=kind)
