"""«Смета»: the cost of a product from its assembly report and the norms and prices of
the plant.

Every plant has its own norms: Fluxion starts from recommended ones (typical general
machine-building time norms, enlarged; current average metal prices) and the user
corrects any value («Инструменты → Нормы и цены завода…»); they are kept in
%APPDATA%\\Fluxion\\plant_norms.json. A value that differs from the recommended one is
the plant's; the estimate says which it is built on.

- Metal: the mass of the details by material (report.metal) × ₽/kg, plus the waste
  of its sortament. The first rule whose pattern is in the material wins.
- Fasteners and bought units: ₽ apiece by the name (the longest pattern found in the
  name wins); a fastener without a price — by its mass × ₽/kg of hardware.
- Work: the time per unit of each operation × its quantity × the rate (welding by
  the length of the seams, scaled by the leg: the deposited metal grows as its square). The
  quantities are what the report knows — sheet details, bends of the build tree,
  the length of the welds, the painted surface, the details, the components; an
  operation whose quantity is not known says «нет данных» instead of a guess.
- Overhead and profit, % of the cost.

What has no price or norm is listed as «Не задано», never counted as zero silently.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .assembly import material_text

PRO_FEATURE = "assembly_report"  # part of the assembly report: Fluxion Pro
BASIS = ("Рекомендуемые нормы — укрупнённые типовые общемашиностроительные нормативы времени "
         "(резка, гибка, сварка, окраска, слесарные и сборочные работы) и средние цены металла на 2026 год. "
         "Исправьте любое значение под свой завод.")
WELD_BASE_LEG = 6.0  # the norm of welding is for a seam with this leg, mm

# the operations and how the report measures them
OPERATIONS = (
    ("cut", "Резка листа", "деталь из листа"),
    ("bend", "Гибка", "гиб"),
    ("weld", "Сварка", "м шва (катет 6)"),
    ("paint", "Покраска с подготовкой", "м²"),
    ("fitting", "Слесарная обработка", "деталь"),
    ("assembly", "Сборка", "компонент"),
)

RECOMMENDED = {
    "_": "Нормы и цены завода для сметы. Правьте в Fluxion: Инструменты → «Нормы и цены завода…» "
         "или здесь. Металл: правило — слово или регулярное выражение, которое ищется в материале "
         "(первое подошедшее). Работы: часов на единицу; ставка 0 — общая ставка нормо-часа. "
         "Сварка — на метр шва с катетом 6 мм.",
    "rate": 1200.0,
    "hardware_price_kg": 300.0,
    "overhead_pct": 0.0,
    "profit_pct": 0.0,
    "metal": [
        {"match": "12х18|08х18|aisi|нерж", "name": "Нержавеющая сталь", "price_kg": 380.0, "waste_pct": 15.0},
        {"match": "амг|д16|ад31|алюм", "name": "Алюминиевые сплавы", "price_kg": 450.0, "waste_pct": 15.0},
        {"match": "09г2с|10хснд|345|390", "name": "Лист низколегированный", "price_kg": 105.0, "waste_pct": 15.0},
        {"match": "лист", "name": "Лист стальной", "price_kg": 90.0, "waste_pct": 15.0},
        {"match": "труб|30245", "name": "Трубы и профильные трубы", "price_kg": 100.0, "waste_pct": 5.0},
        {"match": "уголок|швеллер|двутавр|балка|8509|8240|8239", "name": "Прокат фасонный",
         "price_kg": 95.0, "waste_pct": 5.0},
        {"match": "круг|2590|пруток", "name": "Круг", "price_kg": 100.0, "waste_pct": 10.0},
        {"match": "", "name": "Прочий металл", "price_kg": 100.0, "waste_pct": 5.0},
    ],
    "operations": [
        {"key": "cut", "hours": 0.1, "rate": 0.0},
        {"key": "bend", "hours": 0.03, "rate": 0.0},
        {"key": "weld", "hours": 0.4, "rate": 0.0},
        {"key": "paint", "hours": 0.15, "rate": 0.0},
        {"key": "fitting", "hours": 0.25, "rate": 0.0},
        {"key": "assembly", "hours": 0.1, "rate": 0.0},
    ],
    "items": [],  # {"match": "Ось 35т", "price": 45000.0}
    # «Проверка развёрток» (cad/dxf_check.py): the least sizes in thicknesses of the sheet
    "sheet_limits": {"hole": 1.0, "edge": 1.0, "between": 1.0, "slot": 1.0},
    # the sheets of the purchase list (cad/purchase.py): the first format the thickness fits; 0 — any
    "sheet_formats": [
        {"max_thickness": 4.0, "width": 1250.0, "length": 2500.0},
        {"max_thickness": 0.0, "width": 1500.0, "length": 6000.0},
    ],
}


def norms_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Fluxion" / "plant_norms.json"


def _float(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ".").replace(" ", "")) if value not in (None, "") else default
    except ValueError:
        return default


def normalize(data: dict) -> dict:
    """A norms dict with every field present and numbers as numbers (a hand-edited file)."""
    data = data if isinstance(data, dict) else {}
    out = {
        "_": RECOMMENDED["_"],
        "rate": _float(data.get("rate")),
        "hardware_price_kg": _float(data.get("hardware_price_kg")),
        "overhead_pct": _float(data.get("overhead_pct")),
        "profit_pct": _float(data.get("profit_pct")),
        "metal": [],
        "operations": [],
        "items": [],
    }
    for m in data.get("metal") or []:
        if isinstance(m, dict) and (m.get("name") or m.get("match")):
            out["metal"].append({"match": str(m.get("match") or "").strip(), "name": str(m.get("name") or "").strip(),
                                 "price_kg": _float(m.get("price_kg")), "waste_pct": _float(m.get("waste_pct"))})
    given = {str(o.get("key")): o for o in data.get("operations") or [] if isinstance(o, dict)}
    for key, _name, _unit in OPERATIONS:
        o = given.get(key, {})
        out["operations"].append({"key": key, "hours": _float(o.get("hours")), "rate": _float(o.get("rate"))})
    for i in data.get("items") or []:
        if isinstance(i, dict) and str(i.get("match") or "").strip():
            out["items"].append({"match": str(i["match"]).strip(), "price": _float(i.get("price"))})
    limits = data.get("sheet_limits") if isinstance(data.get("sheet_limits"), dict) else {}
    out["sheet_limits"] = {k: _float(limits.get(k), v) if k in limits else v
                           for k, v in RECOMMENDED["sheet_limits"].items()}
    formats = data.get("sheet_formats")
    if not isinstance(formats, list):  # a file of 0.25 before the purchase list: the recommended formats
        formats = RECOMMENDED["sheet_formats"]
    out["sheet_formats"] = [{"max_thickness": _float(f.get("max_thickness")), "width": _float(f.get("width")),
                             "length": _float(f.get("length"))}
                            for f in formats if isinstance(f, dict) and _float(f.get("width")) > 0]
    return out


def load_norms(create: bool = True) -> dict:
    """The norms of the plant; the recommended ones are written on first use so they can be edited."""
    path = norms_path()
    try:
        return normalize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if create:
        save_norms(RECOMMENDED)
    return normalize(RECOMMENDED)


def recommended() -> dict:
    return normalize(RECOMMENDED)


def changed(norms: dict) -> list[str]:
    """What differs from the recommended norms («Металл: Лист стальной», «Сварка», «Ставка»)."""
    norms, rec = normalize(norms), recommended()
    out = []
    for key, title in (("rate", "Ставка нормо-часа"), ("hardware_price_kg", "Крепёж по массе"),
                       ("overhead_pct", "Накладные"), ("profit_pct", "Рентабельность")):
        if norms[key] != rec[key]:
            out.append(title)
    rec_metal = {(m["match"], m["name"]): m for m in rec["metal"]}
    for m in norms["metal"]:
        if rec_metal.get((m["match"], m["name"])) != m:
            out.append(f"Металл: {m['name'] or m['match']}")
    if len(norms["metal"]) < len(rec["metal"]):
        out.append("Металл: удалены правила")
    names = {key: name for key, name, _unit in OPERATIONS}
    for o, r in zip(norms["operations"], rec["operations"]):
        if o != r:
            out.append(names[o["key"]])
    if norms["items"]:
        out.append(f"Цены покупных и крепежа: {len(norms['items'])}")
    if norms["sheet_formats"] != rec["sheet_formats"]:
        out.append("Форматы листа")
    if norms["sheet_limits"] != rec["sheet_limits"]:
        out.append("Проверка развёрток")
    return out


def basis(norms: dict) -> str:
    """«по рекомендуемым нормам» or «по нормам завода» — what the estimate is built on."""
    return "по нормам завода" if changed(norms) else "по рекомендуемым нормам"


def save_norms(norms: dict) -> Path:
    data = normalize(norms)
    path = norms_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


# ── matching ─────────────────────────────────────────────────────────────────


def _pattern(text: str) -> re.Pattern | None:
    if not text:
        return None
    try:
        return re.compile(text, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(text), re.IGNORECASE)


def metal_rule(material: str, norms: dict) -> dict | None:
    """The first metal rule whose pattern is found in *material*; an empty pattern takes anything."""
    text = material_text(material).lower().replace("ё", "е")
    for rule in norms["metal"]:
        pattern = _pattern(rule["match"].lower().replace("ё", "е"))
        if pattern is None or pattern.search(text):
            return rule
    return None


def item_price(name: str, norms: dict) -> float | None:
    """₽ apiece of a fastener or a bought unit: the longest pattern found in the name."""
    text = " ".join((name or "").lower().replace("ё", "е").split())
    best, best_len = None, -1
    for item in norms["items"]:
        match = " ".join(item["match"].lower().replace("ё", "е").split())
        if match and match in text and len(match) > best_len:
            best, best_len = item["price"], len(match)
    return best


# ── the estimate ─────────────────────────────────────────────────────────────


@dataclass
class Row:
    section: str  # «Металл», «Крепёж», «Покупные», «Работы»
    name: str
    qty: float
    unit: str
    price: float | None  # ₽ per unit (work: ₽ per hour); None — not set
    hours: float = 0.0  # work only
    note: str = ""

    @property
    def missing(self) -> bool:
        return self.price is None

    @property
    def total(self) -> float:
        if self.price is None:
            return 0.0
        return (self.hours if self.section == WORK else self.qty) * self.price


METAL, HARDWARE, BOUGHT, WORK = "Металл", "Крепёж и стандартные изделия", "Покупные", "Работы"


@dataclass
class Estimate:
    rows: list[Row] = field(default_factory=list)
    overhead_pct: float = 0.0
    profit_pct: float = 0.0
    basis: str = "по рекомендуемым нормам"

    def subtotal(self, section: str | None = None) -> float:
        return sum(r.total for r in self.rows if section is None or r.section == section)

    @property
    def hours(self) -> float:
        return sum(r.hours for r in self.rows if r.section == WORK)

    @property
    def overhead(self) -> float:
        return self.subtotal() * self.overhead_pct / 100

    @property
    def profit(self) -> float:
        return (self.subtotal() + self.overhead) * self.profit_pct / 100

    @property
    def total(self) -> float:
        return self.subtotal() + self.overhead + self.profit

    @property
    def missing(self) -> list[Row]:
        return [r for r in self.rows if r.missing]


def bends(features: list[str]) -> int:
    """Bends of a detail by its build tree: each «Сгиб», «Сгиб по линии», «Подсечка» is one."""
    return sum(1 for f in features if re.match(r"^(сгиб|подсечк)", f, re.IGNORECASE))


def weld_metres(report) -> float:
    """Metres of seam on the product, brought to the leg of the norm (6 mm): a seam of
    leg k counts (k/6)² of its length, not less than 0.6 (the setup does not shrink)."""
    total = 0.0
    for w in report.welds:
        for j in w.joints:
            factor = max(0.6, (j.leg_mm / WELD_BASE_LEG) ** 2) if j.leg_mm > 0 else 1.0
            total += j.length_mm * j.count * w.qty_product * factor / 1000
    return total


def sheet_detail(c) -> bool:
    if c.sheet_thickness > 0:
        return True
    try:
        from dxfka.utils import thickness_from_material

        return bool(thickness_from_material(c.material))
    except Exception:
        return False


def build(report, norms: dict | None = None) -> Estimate:
    """The estimate of one product of *report* (cad.report.Report)."""
    from .workload import paint

    norms = normalize(norms) if norms is not None else load_norms()
    est = Estimate(overhead_pct=norms["overhead_pct"], profit_pct=norms["profit_pct"], basis=basis(norms))

    for m in report.metal:
        if m["material"] == "Материал не указан":
            est.rows.append(Row(METAL, m["material"], m["mass_kg"], "кг", None,
                                note="задайте материал деталям — нормоконтроль отчёта"))
            continue
        rule = metal_rule(m["material"], norms)
        if rule is None or not rule["price_kg"]:
            est.rows.append(Row(METAL, m["material"], m["mass_kg"], "кг", None, note="нет цены металла"))
            continue
        mass = m["mass_kg"] * (1 + rule["waste_pct"] / 100)
        est.rows.append(Row(METAL, m["material"], mass, "кг", rule["price_kg"],
                            note=f"{rule['name']}, отход {rule['waste_pct']:g} %"))

    for line in report.standard:
        c = line.component
        price = item_price(c.name, norms)
        if price is not None:
            est.rows.append(Row(HARDWARE, c.name, line.qty_product, "шт", price))
        elif norms["hardware_price_kg"] and c.mass_kg > 0:
            est.rows.append(Row(HARDWARE, c.name, line.qty_product, "шт", c.mass_kg * norms["hardware_price_kg"],
                                note="по массе"))
        else:
            est.rows.append(Row(HARDWARE, c.name, line.qty_product, "шт", None, note="нет цены"))

    for line in report.other:
        c = line.component
        price = item_price(c.name, norms) or (item_price(c.marking, norms) if c.marking else None)
        est.rows.append(Row(BOUGHT, c.title, line.qty_product, "шт", price, note="" if price is not None else "нет цены"))

    details = report.details
    measures = {
        "cut": sum(l.qty_product for l in details if sheet_detail(l.component)),
        "bend": sum(bends(l.component.features) * l.qty_product for l in details),
        "weld": weld_metres(report) if report.welds else None,
        "paint": paint(report).paint_m2,
        "fitting": sum(l.qty_product for l in details if not sheet_detail(l.component)),
        "assembly": sum(line.qty_product for s in report.sections for line in s.lines),
    }
    names = {key: (name, unit) for key, name, unit in OPERATIONS}
    for op in norms["operations"]:
        name, unit = names[op["key"]]
        qty = measures[op["key"]]
        rate = op["rate"] or norms["rate"]
        if qty is None:
            est.rows.append(Row(WORK, name, 0, unit, None, note="нет данных: швы не посчитаны"))
            continue
        if not qty:
            continue
        if not op["hours"] or not rate:
            est.rows.append(Row(WORK, name, qty, unit, None, note="нет нормы времени или ставки"))
            continue
        est.rows.append(Row(WORK, name, qty, unit, rate, hours=qty * op["hours"],
                            note=f"{op['hours']:g} ч на {unit}"))
    return est


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def summary_line(est: Estimate) -> str:
    total = est.total
    parts = []
    for section in (METAL, HARDWARE, BOUGHT, WORK):
        value = est.subtotal(section)
        if value and total and value / total >= 0.005:
            parts.append(f"{section.split()[0].lower()} {value / total:.0%}")
    text = f"Себестоимость ≈ {money(total)} на изделие"
    if parts:
        text += " (" + ", ".join(parts) + f"; {est.hours:,.1f} нормо-ч)".replace(",", " ")
    if est.missing:
        text += f"; не задано: {len(est.missing)} поз."
    text += f" — {est.basis}"
    if est.basis == "по рекомендуемым нормам":
        text += " (исправьте под свой завод: Инструменты → «Нормы и цены завода…»)"
    return text + "."


# ── Excel ────────────────────────────────────────────────────────────────────


def write_sheet(sheet, est: Estimate, bold, head_fill, wrap) -> None:
    """The sheet «Смета» of the report workbook: the rows, the totals, the batch with formulas
    (type the number of products in the batch and the totals follow)."""
    from openpyxl.styles import Font, PatternFill

    ws = sheet("Смета", ["Раздел", "Наименование", "Кол.", "Ед.", "Цена, ₽", "Часы", "Сумма, ₽", "Примечание"],
               [26, 60, 10, 14, 12, 9, 14, 46])
    note = f"Смета {est.basis}."
    if est.basis == "по рекомендуемым нормам":
        note += " Исправьте их под свой завод: Инструменты → «Нормы и цены завода…»."
    ws.cell(row=2, column=1, value=note).font = Font(bold=True, color="B45309")
    missing_fill = PatternFill("solid", fgColor="FDECC8")
    first = ws.max_row + 1
    for r in est.rows:
        ws.append([r.section, r.name, r.qty, r.unit, r.price, r.hours or None, None, r.note])
        row = ws.max_row
        if r.missing:
            for cell in ws[row]:
                cell.fill = missing_fill
            ws.cell(row=row, column=7, value=0)
        elif r.section == WORK:
            ws.cell(row=row, column=7, value=f"=E{row}*F{row}")
        else:
            ws.cell(row=row, column=7, value=f"=C{row}*E{row}")
        ws.cell(row=row, column=8).alignment = wrap
    last = ws.max_row
    ws.append([])
    total_row = ws.max_row + 1
    ws.append(["Итого прямые затраты", "", "", "", "", f"=SUM(F{first}:F{last})", f"=SUM(G{first}:G{last})"])
    ws.append([f"Накладные, {est.overhead_pct:g} %", "", "", "", "", "", f"=G{total_row}*{est.overhead_pct / 100}"])
    ws.append([f"Рентабельность, {est.profit_pct:g} %", "", "", "", "", "",
               f"=(G{total_row}+G{total_row + 1})*{est.profit_pct / 100}"])
    ws.append(["Себестоимость изделия", "", "", "", "", "", f"=SUM(G{total_row}:G{total_row + 2})"])
    product_row = ws.max_row
    ws.append(["Изделий в партии", "", 1, "шт", "", "", "", "введите своё число — итог пересчитается"])
    batch_row = ws.max_row
    ws.append(["Себестоимость партии", "", "", "", "", f"=F{total_row}*C{batch_row}",
               f"=G{product_row}*C{batch_row}"])
    for row in range(total_row, ws.max_row + 1):
        ws.cell(row=row, column=1).font = bold
    for row in ws.iter_rows(min_row=first):
        row[2].number_format = "0.###"
        row[4].number_format = row[6].number_format = "#,##0.00"
        row[5].number_format = "0.0"
    if est.missing:
        ws.append([])
        ws.append(["Не задано", "Что", "", "", "", "", "", "Где вписать"])
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = bold, head_fill
        for r in est.missing:
            where = ("Нормы и цены завода → «Покупные и крепёж»" if r.section in (HARDWARE, BOUGHT)
                     else "Нормы и цены завода → «Металл»" if r.section == METAL
                     else "Нормы и цены завода → «Работы»")
            ws.append([r.section, r.name, "", "", "", "", "", f"{r.note}; {where}"])


# ── the norms in Excel: fill them in in the department, carry them to another computer ──

SHEET_CHECKS = (("hole", "Диаметр отверстия"), ("edge", "Перемычка от отверстия до края"),
                ("between", "Перемычка между отверстиями"), ("slot", "Ширина выреза или паза"))
GENERAL = (("rate", "Ставка нормо-часа, ₽/ч"), ("hardware_price_kg", "Крепёж без цены, ₽/кг"),
           ("overhead_pct", "Накладные, %"), ("profit_pct", "Рентабельность, %"))


def export_xlsx(norms: dict, path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    norms = normalize(norms)
    wb = Workbook()
    ws = wb.active
    ws.title = "Металл"
    ws.append(["Правило (что искать в материале)", "Название", "Цена, ₽/кг", "Отход, %"])
    for m in norms["metal"]:
        ws.append([m["match"], m["name"], m["price_kg"], m["waste_pct"]])
    ws = wb.create_sheet("Работы")
    ws.append(["Код", "Операция", "Единица", "Часов на единицу", "Ставка, ₽/ч (0 — общая)"])
    names = {key: (name, unit) for key, name, unit in OPERATIONS}
    for o in norms["operations"]:
        ws.append([o["key"], *names[o["key"]], o["hours"], o["rate"]])
    ws = wb.create_sheet("Покупные и крепёж")
    ws.append(["Наименование (часть)", "Цена, ₽/шт"])
    for i in norms["items"]:
        ws.append([i["match"], i["price"]])
    ws = wb.create_sheet("Форматы листа")
    ws.append(["До толщины, мм (0 — любая)", "Ширина, мм", "Длина, мм"])
    for f in norms["sheet_formats"]:
        ws.append([f["max_thickness"], f["width"], f["length"]])
    ws = wb.create_sheet("Лист")
    ws.append(["Проверка развёрток", "Не меньше, толщин листа", "Код"])
    for key, title in SHEET_CHECKS:
        ws.append([title, norms["sheet_limits"][key], key])
    ws = wb.create_sheet("Общее")
    ws.append(["Параметр", "Значение", "Код"])
    for key, title in GENERAL:
        ws.append([title, norms[key], key])
    for sheet in wb.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for col, width in zip("ABCDE", (40, 30, 16, 18, 22)):
            sheet.column_dimensions[col].width = width
    path = Path(path)
    wb.save(path)
    return path


def import_xlsx(path: str | Path, base: dict | None = None) -> dict:
    """The norms from a workbook made by export_xlsx (a sheet left out keeps *base*'s)."""
    from openpyxl import load_workbook

    norms = normalize(base if base is not None else load_norms(create=False))
    wb = load_workbook(path, read_only=True, data_only=True)

    def rows(name: str):
        if name not in wb.sheetnames:
            return None
        return [r for r in list(wb[name].iter_rows(values_only=True))[1:] if any(v not in (None, "") for v in r)]

    metal = rows("Металл")
    if metal is not None:
        norms["metal"] = [{"match": str(r[0] or ""), "name": str(r[1] or ""), "price_kg": _float(r[2]),
                           "waste_pct": _float(r[3])} for r in metal]
    work = rows("Работы")
    if work is not None:
        given = {str(r[0]): r for r in work}
        for o in norms["operations"]:
            if o["key"] in given:
                r = given[o["key"]]
                o["hours"], o["rate"] = _float(r[3]), _float(r[4] if len(r) > 4 else 0)
    items = rows("Покупные и крепёж")
    if items is not None:
        norms["items"] = [{"match": str(r[0]).strip(), "price": _float(r[1])} for r in items if r[0]]
    formats = rows("Форматы листа")
    if formats is not None:
        norms["sheet_formats"] = [{"max_thickness": _float(r[0]), "width": _float(r[1]), "length": _float(r[2])}
                                  for r in formats]
    sheet_rows = rows("Лист")
    if sheet_rows is not None:
        for r in sheet_rows:
            key = str(r[2] or "") if len(r) > 2 else ""
            if key in norms["sheet_limits"]:
                norms["sheet_limits"][key] = _float(r[1])
    general = rows("Общее")
    if general is not None:
        for r in general:
            key = str(r[2] or "") if len(r) > 2 else ""
            if key in dict(GENERAL):
                norms[key] = _float(r[1])
    return normalize(norms)
