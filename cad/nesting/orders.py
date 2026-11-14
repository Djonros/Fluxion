"""The order of a nesting: the details to cut, read from an Excel sheet as the plant keeps it.

The first rows hold the headers; their order does not matter, a column is known by its
title: «Обозначение», «Наименование», «Материал» («Лист 8 (09Г2С)»), the quantity —
«Кол-во на 1 шт.» times the number of products, or one «Кол-во» for the whole order —
and, if there are, «Толщина», «Марка», «Файл DXF» / «DXF», «Поворот».

«(Г)», «(Г в св)», «Г на стороне» in the name mark a bent detail: its DXF must be a flat
pattern. The number of the order is the leading number of the file name («762000-1шт…»),
the number of products — «-5шт» there, else 1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .materials import SheetMaterial, normalize_grade, parse

HEADER_ROWS = 15  # the headers are looked for in the first rows
_BENT_RE = re.compile(r"\(\s*г\b|\bг\s+(?:в\s*св|всв|на\s+сторон)", re.IGNORECASE)


@dataclass
class OrderItem:
    row: int  # in the sheet, from 1
    designation: str
    name: str
    material_text: str
    material: SheetMaterial | None
    per_product: int  # the quantity on one product (or on the order when the sheet has one column)
    quantity: int  # to cut
    dxf: str = ""  # a path given in the sheet
    rotate: bool = True
    bent: bool = False

    @property
    def title(self) -> str:
        return f"{self.designation} {self.name}".strip()


@dataclass
class Order:
    path: Path
    number: str
    products: int
    items: list[OrderItem] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # rows skipped, and why

    def materials(self) -> list[SheetMaterial]:
        seen: list[SheetMaterial] = []
        for item in self.items:
            if item.material and item.material not in seen:
                seen.append(item.material)
        return sorted(seen, key=lambda m: (m.thickness, m.checker, m.grade))


def order_number(path: str | Path) -> str:
    match = re.match(r"\s*(\d{3,})", Path(path).stem)
    return match.group(1) if match else ""


def products_in_name(path: str | Path) -> int:
    match = re.search(r"(\d+)\s*шт", Path(path).stem, re.IGNORECASE)
    return max(1, int(match.group(1))) if match else 1


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return float(match.group().replace(",", ".")) if match else None


def read_rows(path: str | Path) -> list[list]:
    """The cells of the first sheet that has rows (formulas as Excel last computed them)."""
    import openpyxl

    book = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        for sheet in book.worksheets:
            rows = [list(r) for r in sheet.iter_rows(values_only=True)]
            if any(any(v not in (None, "") for v in r) for r in rows):
                return rows
        return []
    finally:
        book.close()


@dataclass
class Columns:
    designation: int = -1
    name: int = -1
    material: int = -1
    thickness: int = -1
    grade: int = -1
    per_product: int = -1  # «Кол-во на 1 шт.»
    quantity: int = -1  # «Кол-во» / «Кол-во на 5 шт.»
    quantity_of: int = 0  # the N of «на N шт.» of that column; 0 — the whole order
    dxf: int = -1
    rotate: int = -1


def find_columns(row: list) -> Columns | None:
    """The columns of a header row; None when it has no designation or no quantity."""
    found = Columns()
    for i, cell in enumerate(row):
        title = _text(cell).lower().replace("ё", "е")
        if not title:
            continue
        if "обознач" in title and found.designation < 0:
            found.designation = i
        elif "наименов" in title and found.name < 0:
            found.name = i
        elif "dxf" in title or ("файл" in title and found.dxf < 0):
            found.dxf = i
        elif "поворот" in title:
            found.rotate = i
        elif "толщин" in title and found.thickness < 0:
            found.thickness = i
        elif "марка" in title and found.grade < 0:
            found.grade = i
        elif "материал" in title and found.material < 0:
            found.material = i
        elif title.startswith("кол") or "количеств" in title:
            per = re.search(r"на\s*(\d+)\s*(?:шт|изд|компл)", title)
            if per and int(per.group(1)) == 1:
                found.per_product = i
            elif found.quantity < 0:
                found.quantity = i
                found.quantity_of = int(per.group(1)) if per else 0
    if found.designation < 0 or (found.per_product < 0 and found.quantity < 0):
        return None
    return found


def _rotation_allowed(text: str) -> bool:
    lowered = text.lower()
    return not re.search(r"нет|запрещ|нельзя|без|прокат|^-$", lowered)


def read_order(path: str | Path, products: int | None = None) -> Order:
    """The details of an order. *products* — how many products to cut (by default from
    the file name); with «Кол-во на 1 шт.» the quantity is that times *products*."""
    path = Path(path)
    count = products if products and products > 0 else products_in_name(path)
    order = Order(path, order_number(path), count)
    rows = read_rows(path)
    header, columns = -1, None
    for i, row in enumerate(rows[:HEADER_ROWS]):
        columns = find_columns(row)
        if columns:
            header = i
            break
    if columns is None:
        order.problems.append("Не нашёл строку заголовков: нужны колонки «Обозначение» и «Кол-во».")
        return order

    def cell(row: list, index: int):
        return row[index] if 0 <= index < len(row) else None

    for number, row in enumerate(rows[header + 1:], start=header + 2):
        designation = _text(cell(row, columns.designation))
        if not designation:
            continue
        name = _text(cell(row, columns.name))
        if columns.per_product >= 0:
            per = _number(cell(row, columns.per_product))
            quantity = None if per is None else per * count
        else:
            per = _number(cell(row, columns.quantity))
            quantity = per
            if per is not None and columns.quantity_of > 1:  # «на 5 шт.» for 1 product: scale
                per = per / columns.quantity_of
                quantity = per * count
        if per is None or quantity is None or quantity <= 0:
            order.problems.append(f"Строка {number}: {designation} — нет количества, пропущена.")
            continue
        material_text = _text(cell(row, columns.material))
        material = parse(material_text)
        thickness = _number(cell(row, columns.thickness))
        grade = _text(cell(row, columns.grade))
        if thickness:  # a column of its own wins over the text of the material
            material = SheetMaterial(thickness, normalize_grade(grade) if grade else
                                     (material.grade if material else ""), bool(material and material.checker))
        elif material and grade:
            material = SheetMaterial(material.thickness, normalize_grade(grade), material.checker)
        if material is None:
            order.problems.append(f"Строка {number}: {designation} — не понял толщину из «{material_text}».")
        order.items.append(OrderItem(
            row=number, designation=designation, name=name, material_text=material_text or grade,
            material=material, per_product=round(per), quantity=round(quantity),
            dxf=_text(cell(row, columns.dxf)), rotate=_rotation_allowed(_text(cell(row, columns.rotate))),
            bent=bool(_BENT_RE.search(name))))
    if not order.items:
        order.problems.append("В заявке нет деталей с количеством.")
    return order
