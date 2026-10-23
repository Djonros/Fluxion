"""Reports on an assembly read by cad/assembly.py, saved as an Excel workbook.

- «Сводная ведомость»: every assembly of the product (the product itself and
  its subassemblies at all levels) with its components, quantity in the
  assembly and on the product, mass of one and of all.
- «Детали»: every detail of the product once, with the quantity on the product.
- «Металлоёмкость»: mass of the details by material and sortament.
- «Замечания»: what a norm controller would return: no designation, no
  material, zero mass.

Quantities: identical components (same designation, name, material, mass and
file) of one assembly are one line; the quantity on the product multiplies the
quantities down the tree.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .assembly import (ASSEMBLY, DETAIL, KIND_TITLES, OTHER, STANDARD, Component, material_text, no_material,
                       purchased_by_name)

NO_MATERIAL = "Материал не указан"
# order of the sections of a KOMPAS specification (ГОСТ 2.106)
SECTION_ORDER = ("Документация", "Комплексы", "Сборочные единицы", "Детали", "Стандартные изделия",
                 "Прочие изделия", "Материалы", "Комплекты")
SECTION_BY_KIND = {ASSEMBLY: "Сборочные единицы", DETAIL: "Детали", STANDARD: "Стандартные изделия",
                   OTHER: "Прочие изделия"}


@dataclass
class Line:
    component: Component
    qty: int  # in the assembly of the section (or on the product)
    qty_product: int = 0


@dataclass
class Section:
    assembly: Component
    qty_product: int
    lines: list[Line] = field(default_factory=list)


@dataclass
class Report:
    top: Component
    sections: list[Section]
    details: list[Line]  # each detail once, qty_product
    standard: list[Line]
    other: list[Line]  # «Прочие изделия»: bought units
    metal: list[dict]
    remarks: list[dict]
    # assemblies whose mass in KOMPAS is more than the mass of their components:
    # bought units (an axle, a hose) with the mass set by hand; (section, own kg)
    assigned: list[tuple["Section", float]] = field(default_factory=list)
    welds: list = field(default_factory=list)  # cad.welds.WeldedAssembly, filled by add_welds
    decisions: dict = field(default_factory=dict)  # the designer's marks: remark id -> {"checked", "purchased"}

    @property
    def unchecked(self) -> list[dict]:
        """Warnings the designer has not marked yet: the document is not made until none are left."""
        return [r for r in self.remarks if not r["checked"]]

    @property
    def weld_length_m(self) -> float:
        return sum(w.length_mm * w.qty_product for w in self.welds) / 1000

    @property
    def weld_mass_kg(self) -> float:
        return sum(w.mass_g * w.qty_product for w in self.welds) / 1000

    @property
    def assigned_mass_kg(self) -> float:
        return sum(own * section.qty_product for section, own in self.assigned)

    @property
    def details_mass_kg(self) -> float:
        return sum(line.component.mass_kg * line.qty_product for line in self.details)

    @property
    def standard_mass_kg(self) -> float:
        return sum(line.component.mass_kg * line.qty_product for line in self.standard)

    @property
    def other_mass_kg(self) -> float:
        return sum(line.component.mass_kg * line.qty_product for line in self.other)


@dataclass
class TreeRow:
    """A line of the statement in the shape of the build tree."""

    number: str  # "1.2.3": place in the tree
    depth: int  # 0 — the product
    component: Component
    qty: int  # in its assembly
    qty_product: int

    @property
    def section(self) -> str:
        return self.component.section or SECTION_BY_KIND.get(self.component.kind, "")


def _spec_order(item: tuple[Component, int]) -> tuple:
    c = item[0]
    section = c.section or SECTION_BY_KIND.get(c.kind, "")
    rank = SECTION_ORDER.index(section) if section in SECTION_ORDER else len(SECTION_ORDER)
    pos = c.position.strip()
    return (rank, 0 if pos.isdigit() else 1, int(pos) if pos.isdigit() else 0, c.marking, c.name)


def tree(top: Component) -> list[TreeRow]:
    """The product as its build tree: every assembly followed by its components, ordered as
    in the specification (section, then position); identical components are one row."""
    rows = [TreeRow("1", 0, top, 1, 1)]

    def walk(assembly: Component, number: str, depth: int, mult: int) -> None:
        for k, (child, n) in enumerate(sorted(_grouped(assembly.children), key=_spec_order), 1):
            child_number = f"{number}.{k}"
            rows.append(TreeRow(child_number, depth, child, n, n * mult))
            if child.kind == ASSEMBLY:
                walk(child, child_number, depth + 1, n * mult)

    walk(top, "1", 1, 1)
    return rows


def _grouped(children: list[Component]) -> list[tuple[Component, int]]:
    groups: OrderedDict[tuple, list] = OrderedDict()
    for child in children:
        groups.setdefault(child.key, [child, 0])[1] += 1
    return [(c, n) for c, n in groups.values()]


PURCHASED_PROBLEM = "Покупное не в «Прочих изделиях»"


def remark_id(problem: str, title: str, where: str = "") -> str:
    """A warning: the same detail in two assemblies is two places to mark."""
    return f"{problem}|{title}|{where}"


def purchase_key(title: str) -> str:
    """The designer's "bought or made here" answer, one per name."""
    return f"purchased|{title}"


def _guess_purchased(assembly: Component, decisions: dict) -> None:
    """Bought units left out of «Прочие изделия» become «Прочие изделия» of the report
    (and a warning for the designer) unless the designer marked one as made here."""
    for child in assembly.children:
        if purchased_by_name(child):
            if decisions.get(purchase_key(child.title), {}).get("purchased", True):
                child.kind, child.guessed = OTHER, True
                continue
        if child.kind == ASSEMBLY:
            _guess_purchased(child, decisions)


def build(top: Component, decisions: dict | None = None) -> Report:
    """*decisions*: the marks the designer gave the warnings before (load_decisions)."""
    decisions = dict(decisions or {})
    _guess_purchased(top, decisions)
    sections: OrderedDict[tuple, Section] = OrderedDict()
    details: OrderedDict[tuple, Line] = OrderedDict()
    standard: OrderedDict[tuple, Line] = OrderedDict()
    other: OrderedDict[tuple, Line] = OrderedDict()
    where: dict[tuple, str] = {}  # first assembly a component was met in (for the remarks)

    def walk(assembly: Component, mult: int) -> None:
        key = assembly.key
        first = key not in sections
        if first:
            sections[key] = Section(assembly, 0, [Line(c, n) for c, n in _grouped(assembly.children)])
        sections[key].qty_product += mult
        for child, n in _grouped(assembly.children):
            where.setdefault(child.key, assembly.title)
            total = n * mult
            if child.kind == ASSEMBLY:
                walk(child, total)
            else:
                bucket = {DETAIL: details, STANDARD: standard, OTHER: other}[child.kind]
                bucket.setdefault(child.key, Line(child, 0)).qty_product += total

    walk(top, 1)
    assigned = []
    for section in sections.values():
        for line in section.lines:
            line.qty_product = line.qty * section.qty_product
        own = section.assembly.mass_kg - sum(line.component.mass_kg * line.qty for line in section.lines)
        if own > max(0.5, 0.01 * section.assembly.mass_kg):
            assigned.append((section, own))
    return Report(top, list(sections.values()), list(details.values()), list(standard.values()),
                  list(other.values()), _metal(details.values()),
                  _remarks(sections.values(), details.values(), other.values(), where, decisions), assigned,
                  decisions=decisions)


def _metal(details) -> list[dict]:
    rows: OrderedDict[str, dict] = OrderedDict()
    for line in details:
        c = line.component
        name = NO_MATERIAL if no_material(c.material) else material_text(c.material)
        row = rows.setdefault(name, {"material": name, "items": 0, "qty": 0, "mass_kg": 0.0, "area_m2": 0.0})
        row["items"] += 1
        row["qty"] += line.qty_product
        row["mass_kg"] += c.mass_kg * line.qty_product
        row["area_m2"] += c.area_m2 * line.qty_product
    total = sum(r["mass_kg"] for r in rows.values()) or 1.0
    out = sorted(rows.values(), key=lambda r: (r["material"] == NO_MATERIAL, -r["mass_kg"]))
    for row in out:
        row["share"] = row["mass_kg"] / total
    return out


ATTENTION, CHECK = "Внимание", "Проверьте"


def _remarks(sections, details, other, where, decisions=None) -> list[dict]:
    """Warnings for the designer, shown before the report is made: what will look
    wrong in the specification or spoil the calculation. Each has to be marked."""
    out = []
    decisions = decisions or {}

    def add(c: Component, level: str, problem: str, message: str, qty: int, purchased=None) -> None:
        rid = remark_id(problem, c.title, where.get(c.key, ""))
        n = 2
        while any(r["id"] == rid for r in out):  # the same name twice in one assembly (other file or mass)
            rid = remark_id(problem, c.title, where.get(c.key, "")) + f"#{n}"
            n += 1
        mark = decisions.get(rid, {})
        out.append({"id": rid, "level": level, "problem": problem, "message": message, "kind": KIND_TITLES[c.kind],
                    "title": c.title, "where": where.get(c.key, ""), "qty": qty, "file": c.file,
                    "checked": bool(mark.get("checked")), "purchased": purchased})

    for line in other:
        c = line.component
        if c.guessed:
            add(c, ATTENTION, PURCHASED_PROBLEM,
                f"«{c.title}» по наименованию — покупное изделие, а в модели оно в разделе "
                f"«{c.section or 'Детали'}». Отнесите его к «Прочим изделиям», иначе в спецификации оно "
                "будет не в своём разделе. В отчёте оно считается покупным.", line.qty_product, purchased=True)
    for line in list(details) + [Line(s.assembly, 0, s.qty_product) for s in list(sections)[1:]]:
        c = line.component
        if purchased_by_name(c):  # the designer marked it as made here
            add(c, ATTENTION, PURCHASED_PROBLEM,
                f"«{c.title}» похоже на покупное, но отмечено конструктором как своё изготовление: "
                "в отчёте оно считается деталью.", line.qty_product, purchased=False)
    for section in list(sections)[1:]:
        if not section.assembly.marking:
            add(section.assembly, CHECK, "Сборочная единица без обозначения",
                "В спецификации строка будет без обозначения.", section.qty_product)
    for line in details:
        c = line.component
        if no_material(c.material):
            default = " (стоит «Сталь 10» — материал КОМПАС по умолчанию)" if c.material.strip() else ""
            add(c, ATTENTION, "Не задан материал",
                f"Не задан материал{default}: в спецификации деталь будет отображаться неправильно, "
                "масса и металлоёмкость неверны, швы с ней не считаются.", line.qty_product)
        if not c.marking:
            add(c, CHECK, "Деталь без обозначения", "В спецификации строка будет без обозначения.", line.qty_product)
        if c.mass_g <= 0:
            add(c, CHECK, "Нулевая масса", "Проверьте тело детали и плотность материала.", line.qty_product)
    order = {ATTENTION: 0, CHECK: 1}
    return sorted(out, key=lambda r: order[r["level"]])


# ── the designer's marks, kept per product ──────────────────────────────────


def decisions_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Fluxion" / "report_decisions.json"


def load_decisions(product_file: str) -> dict:
    try:
        data = json.loads(decisions_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    marks = data.get(str(product_file).lower(), {})
    return marks if isinstance(marks, dict) else {}


def save_decisions(product_file: str, marks: dict) -> None:
    """Keep the marks of a product: a warning marked once is not asked about again."""
    path = decisions_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[str(product_file).lower()] = marks
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def decisions_from_marks(report: "Report", marks: dict) -> dict:
    """What to keep for the product: the marks and the "bought or made here" answers."""
    out = dict(marks)
    for r in report.remarks:
        if r["purchased"] is not None:
            out[purchase_key(r["title"])] = {"purchased": marks.get(r["id"], {}).get("purchased", r["purchased"])}
    return out


def purchase_changed(report: "Report", marks: dict) -> bool:
    """Did the designer change a "bought or made here" answer? Then the report is
    built again: the metal report and the welds depend on it."""
    for r in report.remarks:
        if r["purchased"] is not None and marks.get(r["id"], {}).get("purchased", r["purchased"]) != r["purchased"]:
            return True
    return False


def warnings_text(report: "Report", limit: int = 12) -> str:
    """The warnings in a few lines: counts by problem and the first ones in full."""
    if not report.remarks:
        return "Спорных мест не найдено."
    counts: OrderedDict[str, int] = OrderedDict()
    for r in report.remarks:
        counts[f"{r['level']}: {r['problem'].lower()}"] = counts.get(f"{r['level']}: {r['problem'].lower()}", 0) + 1
    lines = [f"Перед оформлением проверьте ({len(report.remarks)}):"]
    lines += [f"• {k} — {v}" for k, v in counts.items()]
    lines.append("")
    for r in report.remarks[:limit]:
        where = f" (в «{r['where']}»)" if r["where"] else ""
        lines.append(f"{r['level']}: {r['title']}{where} — {r['message']}")
    if len(report.remarks) > limit:
        lines.append(f"…и ещё {len(report.remarks) - limit} — полный список на листе «Предупреждения».")
    return "\n".join(lines)


# ── Excel ────────────────────────────────────────────────────────────────────


def default_path(top: Component, folder: Path | None = None) -> Path:
    from .kompas import default_save_dir

    base = Path(folder) if folder else default_save_dir()
    stem = top.marking or top.name or "Сборка"
    for bad in '<>:"/\\|?*':
        stem = stem.replace(bad, "_")
    path = base / f"{stem} - ведомость и металлоёмкость.xlsx"
    for n in range(2, 100):  # an earlier report may be open in Excel: never overwrite
        if not path.exists():
            break
        path = base / f"{stem} - ведомость и металлоёмкость ({n}).xlsx"
    return path


def save_xlsx(report: Report, path: Path, source: str = "") -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="DDE4EE")
    section_fill = PatternFill("solid", fgColor="EEF2F7")
    wrap = Alignment(wrap_text=True, vertical="top")
    top = report.top

    wb = Workbook()

    def sheet(title: str, headers: list[str], widths: list[int], first: bool = False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append([f"{top.title}"])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([f"Масса изделия {top.mass_kg:,.1f} кг · {datetime.now():%d.%m.%Y %H:%M}"
                   + (f" · {source}" if source else "")])
        ws.append([])
        ws.append(headers)
        for col, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
            cell = ws.cell(row=4, column=col)
            cell.font, cell.fill, cell.alignment = bold, head_fill, wrap
        ws.freeze_panes = "A5"
        return ws

    kg, m2 = "#,##0.00", "#,##0.000"

    ws = sheet("Сводная ведомость",
               ["№", "Поз.", "Обозначение", "Наименование", "Раздел спецификации", "Материал", "Кол. в сборке",
                "Кол. на изделие", "Масса 1 шт, кг", "Масса всего, кг"],
               [12, 6, 24, 46, 20, 46, 10, 10, 14, 15], first=True)
    # the build tree: a subassembly is a group of rows that Excel folds (the +/- on the left)
    ws.sheet_properties.outlinePr.summaryBelow = False
    for row in tree(top):
        c = row.component
        material = "" if c.kind == ASSEMBLY else material_text(c.material)  # an assembly has "Сталь 10" by default
        ws.append([row.number, c.position, c.marking, c.name, row.section, material,
                   row.qty if row.depth else "", row.qty_product, c.mass_kg, c.mass_kg * row.qty_product])
        r = ws.max_row
        ws.cell(row=r, column=4).alignment = Alignment(indent=min(row.depth * 2, 15), wrap_text=True,
                                                       vertical="top")
        if row.depth:
            ws.row_dimensions[r].outline_level = min(row.depth, 7)
        if c.kind == ASSEMBLY:
            for cell in ws[r]:
                cell.font, cell.fill = bold, section_fill
    for row in ws.iter_rows(min_row=5):
        row[8].number_format = row[9].number_format = kg
        row[5].alignment = wrap

    ws = sheet("Детали", ["Обозначение", "Наименование", "Материал", "Кол. на изделие", "Масса 1 шт, кг",
                          "Масса всего, кг", "Площадь 1 шт, м²", "Площадь всего, м²"],
               [24, 42, 46, 10, 14, 15, 14, 15])
    for line in sorted(report.details, key=lambda l: (l.component.marking or "яя", l.component.name)):
        c = line.component
        ws.append([c.marking, c.name, material_text(c.material), line.qty_product, c.mass_kg,
                   c.mass_kg * line.qty_product, c.area_m2, c.area_m2 * line.qty_product])
    ws.append([])
    ws.append(["", "Итого деталей", "", sum(l.qty_product for l in report.details), "", report.details_mass_kg, "",
               sum(l.component.area_m2 * l.qty_product for l in report.details)])
    ws[ws.max_row][1].font = bold
    for row in ws.iter_rows(min_row=5):
        row[4].number_format = row[5].number_format = kg
        row[6].number_format = row[7].number_format = m2

    ws = sheet("Металлоёмкость", ["Материал / сортамент", "Позиций", "Кол. деталей", "Масса, кг", "Доля",
                                   "Площадь, м²"], [70, 10, 12, 14, 9, 13])
    for row in report.metal:
        ws.append([row["material"], row["items"], row["qty"], row["mass_kg"], row["share"], row["area_m2"]])
    ws.append([])
    ws.append(["Итого детали", sum(r["items"] for r in report.metal), sum(r["qty"] for r in report.metal),
               report.details_mass_kg, 1.0, sum(r["area_m2"] for r in report.metal)])
    ws.append(["Стандартные изделия", len(report.standard), sum(l.qty_product for l in report.standard),
               report.standard_mass_kg])
    ws.append(["Прочие изделия (покупные)", len(report.other), sum(l.qty_product for l in report.other),
               report.other_mass_kg])
    ws.append(["Масса, заданная у сборочных единиц (покупные изделия)", len(report.assigned),
               sum(s.qty_product for s, _ in report.assigned), report.assigned_mass_kg])
    ws.append(["Изделие по КОМПАС-3D", "", "", top.mass_kg])
    for r in range(ws.max_row - 4, ws.max_row + 1):
        ws.cell(row=r, column=1).font = bold
    if report.assigned:
        ws.append([])
        ws.append(["Сборочные единицы с заданной массой", "", "Кол. на изделие", "Масса сверх деталей, кг"])
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = bold, head_fill
        for section, own in sorted(report.assigned, key=lambda x: -x[1] * x[0].qty_product):
            ws.append([section.assembly.title, "", section.qty_product, own * section.qty_product])
    for row in ws.iter_rows(min_row=5):
        row[3].number_format = kg
        row[4].number_format = "0.0%"
        row[5].number_format = m2

    ws = sheet("Прочие изделия", ["Обозначение", "Наименование", "Кол. на изделие", "Масса 1 шт, кг",
                                   "Масса всего, кг", "Где"], [24, 50, 10, 14, 15, 40])
    for line in report.other:
        c = line.component
        ws.append([c.marking, c.name, line.qty_product, c.mass_kg, c.mass_kg * line.qty_product,
                   report_where(report, c)])
    for row in ws.iter_rows(min_row=5):
        row[3].number_format = row[4].number_format = kg

    if report.welds:
        _weld_sheet(sheet, report, bold, head_fill, section_fill, kg)
    _paint_sheet(sheet, report, bold, head_fill)
    _work_sheet(sheet, report, bold, head_fill, section_fill)

    ws = sheet("Предупреждения", ["Уровень", "Замечание", "Обозначение и наименование", "Что будет и что сделать",
                                   "Где", "Кол. на изделие", "Проверено", "Файл"], [12, 32, 46, 70, 40, 10, 11, 60])
    for r in report.remarks:
        ws.append([r["level"], r["problem"], r["title"], r["message"], r["where"], r["qty"],
                   "да" if r["checked"] else "нет", r["file"]])
        if r["level"] == ATTENTION:
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color="B45309")
        for cell in ws[ws.max_row][2:5]:
            cell.alignment = wrap
    if not report.remarks:
        ws.append(["", "Спорных мест не найдено"])
    wb.move_sheet(ws, offset=-(len(wb.sheetnames) - 1))  # the designer sees it first
    wb.active = 0

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _weld_sheet(sheet, report: Report, bold, head_fill, section_fill, kg) -> None:
    from .welds import WELD_FACTOR

    ws = sheet("Сварка", ["Сборочная единица / соединяемые детали", "Кол. сборок на изделие", "Катет, мм",
                          "Длина шва, мм", "Кол. швов в сборке", "Длина в сборке, м", "Металл шва в сборке, кг",
                          "Длина на изделие, м", "Металл шва на изделие, кг"],
               [62, 12, 9, 12, 11, 12, 14, 13, 15])
    for w in report.welds:
        ws.append([w.assembly.title, w.qty_product, "", "", "", w.length_mm / 1000, w.mass_g / 1000,
                   w.length_mm * w.qty_product / 1000, w.mass_g * w.qty_product / 1000])
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = bold, section_fill
        if w.note:
            ws.append([f"   {w.note}"])
        for j in w.joints:
            ws.append([f"   {j.a} × {j.b}", "", j.leg_mm, round(j.length_mm), j.count,
                       j.length_mm * j.count / 1000, j.mass_g / 1000,
                       j.length_mm * j.count * w.qty_product / 1000, j.mass_g * w.qty_product / 1000])
            ws.row_dimensions[ws.max_row].outline_level = 1
    ws.append([])
    ws.append(["Итого на изделие", "", "", "", "", "", "", report.weld_length_m, report.weld_mass_kg])
    ws[ws.max_row][0].font = bold
    # the welding table by leg size
    legs: dict[float, list[float]] = {}
    for w in report.welds:
        for j in w.joints:
            row = legs.setdefault(j.leg_mm, [0.0, 0.0])
            row[0] += j.length_mm * j.count * w.qty_product / 1000
            row[1] += j.mass_g * w.qty_product / 1000
    ws.append([])
    ws.append(["Таблица сварки по катетам", "", "Катет, мм", "", "", "", "", "Длина на изделие, м",
               "Металл шва на изделие, кг"])
    for cell in ws[ws.max_row]:
        cell.font, cell.fill = bold, head_fill
    for leg in sorted(legs):
        ws.append(["", "", leg, "", "", "", "", legs[leg][0], legs[leg][1]])
    ws.append([])
    ws.append([f"Швы найдены по стыкам деталей в модели: плоские грани в одной плоскости с общей площадкой, "
               f"длина шва — периметр площадки. Катет — по наименьшей толщине соединяемых деталей, "
               f"не больше 12 мм; детали без материала (и с «Сталь 10» по умолчанию КОМПАС) и оси не входят. "
               f"Масса металла шва = k²/2 × длина × 7,85 г/см³ × {WELD_FACTOR:g}."])
    for row in ws.iter_rows(min_row=5):
        for cell in row[5:9]:
            cell.number_format = "#,##0.000"


def _paint_sheet(sheet, report: Report, bold, head_fill) -> None:
    from .workload import paint

    p = paint(report)
    ws = sheet("Покраска", ["Материал / сортамент", "Площадь деталей, м²", "Красится"], [70, 18, 10])
    for material, m2, painted in p.by_material:
        ws.append([material, m2, "да" if painted else "нет"])
    ws.append([])
    for label, value in (("Площадь деталей на изделие", p.details_m2),
                         ("Не красятся (нержавейка, алюминий, оцинковка, латунь, пластики, резина)", -p.not_painted_m2),
                         ("Скрыто в сварных стыках (обе грани)", -p.joints_m2),
                         ("Под дробеструйную обработку и окраску", p.paint_m2)):
        ws.append([label, value])
    for r in range(ws.max_row - 3, ws.max_row + 1):
        ws.cell(row=r, column=1).font = bold
    ws.cell(row=ws.max_row, column=1).fill = ws.cell(row=ws.max_row, column=2).fill = head_fill
    ws.append([])
    ws.append(["Покупные и стандартные изделия не входят. Площадь — по телам деталей КОМПАС-3D, на изделие "
               "(с количеством)."])
    for row in ws.iter_rows(min_row=5):
        row[1].number_format = "#,##0.00"


def _work_sheet(sheet, report: Report, bold, head_fill, section_fill) -> None:
    from .workload import load_norms, norms_path, workload

    norms = load_norms()
    rows = workload(report, norms)
    ws = sheet("Трудоёмкость", ["Обозначение и наименование", "Вид", "Операций / компонентов", "Эскизов",
                                "Сложность", "Норма, ч"], [60, 18, 14, 10, 12, 10])
    for r in rows:
        ws.append([r.title, r.kind, r.operations, r.sketches if r.kind == "Деталь" else "", r.category, r.hours])
        if r.kind != "Деталь":
            for cell in ws[ws.max_row]:
                cell.fill = section_fill
    details = [r for r in rows if r.kind == "Деталь"]
    ws.append([])
    for name in [row["category"] for row in norms["detail"]]:
        group = [r for r in details if r.category == name]
        ws.append([f"Детали — {name}", "", len(group), "", "", sum(r.hours for r in group)])
    ws.append(["Сборочные единицы", "", len(rows) - len(details), "", "", sum(r.hours for r in rows if r.kind != "Деталь")])
    ws.append(["Итого на модель изделия", "", "", "", "", sum(r.hours for r in rows)])
    ws[ws.max_row][0].font = ws[ws.max_row][5].font = bold
    ws.append([])
    ws.append([f"Каждая деталь считается один раз (копии — не работа), покупные и стандартные не входят. "
               f"Нормы — в файле {norms_path()}."])
    for row in ws.iter_rows(min_row=5):
        row[5].number_format = "0.00"


def add_welds(report: Report, log=print) -> None:
    """Welds of the welded assemblies (KOMPAS-3D opens each of them read-only)."""
    from . import welds

    try:
        report.welds = welds.compute(report.top, log=log)
    except Exception as exc:  # the statement and the metal report do not depend on it
        report.welds = []
        log(f"Сварка не посчитана: {exc}")


def report_where(report: Report, component: Component) -> str:
    """The assemblies a component is in, for the lists."""
    return "; ".join(dict.fromkeys(
        section.assembly.title for section in report.sections
        if any(line.component.key == component.key for line in section.lines)))


def summary_text(report: Report, path: Path | None = None) -> str:
    """A few lines for the chat or the console."""
    lines = [
        f"{report.top.title}: масса {report.top.mass_kg:,.1f} кг".replace(",", " "),
        f"Сборочных единиц: {len(report.sections) - 1}, деталей: {len(report.details)} поз. "
        f"({sum(l.qty_product for l in report.details)} шт.), стандартных изделий: {len(report.standard)} поз. "
        f"({sum(l.qty_product for l in report.standard)} шт.), прочих изделий: {len(report.other)} поз. "
        f"({sum(l.qty_product for l in report.other)} шт.).",
        "Металлоёмкость (детали): " + "; ".join(
            f"{r['material']} — {r['mass_kg']:,.1f} кг".replace(",", " ") for r in report.metal[:5]) + ".",
    ]
    counted = report.details_mass_kg + report.standard_mass_kg + report.other_mass_kg + report.assigned_mass_kg
    lines.append(
        f"Сверка массы: детали {report.details_mass_kg:,.1f} + стандартные {report.standard_mass_kg:,.1f}"
        f" + прочие {report.other_mass_kg:,.1f} + заданная у сборок {report.assigned_mass_kg:,.1f}"
        f" = {counted:,.1f} кг"
        f" (КОМПАС-3D: {report.top.mass_kg:,.1f} кг).".replace(",", " ")
    )
    try:
        from .workload import paint, workload

        hours = sum(r.hours for r in workload(report))
        lines.append(f"Под дробеструй и окраску: {paint(report).paint_m2:,.1f} м². ".replace(",", " ")
                     + f"Трудоёмкость модели по нормам: {hours:,.1f} ч.".replace(",", " "))
    except Exception:  # an unreadable norms file must not stop the report
        pass
    if report.welds:
        length = f"{report.weld_length_m:,.1f}".replace(",", " ")
        mass = f"{report.weld_mass_kg:,.1f}".replace(",", " ")
        lines.append(f"Сварка: сварных сборочных единиц {len(report.welds)}, швов на изделие {length} м, "
                     f"металл шва {mass} кг.")
    if report.remarks:
        counts: OrderedDict[str, int] = OrderedDict()
        for r in report.remarks:
            counts[r["problem"]] = counts.get(r["problem"], 0) + 1
        lines.append("Предупреждения: " + "; ".join(f"{k.lower()} — {v}" for k, v in counts.items()) + ".")
    if path:
        lines.append(f"Отчёт: {path}")
    return "\n".join(lines)
