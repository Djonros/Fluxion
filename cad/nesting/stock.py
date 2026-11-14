"""The sheets a nesting may use: the remnants of the shop («деловые остатки») from the Excel
list the plant keeps, and the whole sheets.

The list: a header row with «Толщина листа» («8 09Г2С», «4 Чеч-цаСт3») and «Габариты»
(«1250х880»), «Кол-во», and maybe «Эскиз» (a picture of a remnant that is not a
rectangle), «Цветовая маркировка», «Задействованные» (the order the remnant is kept
for). The rows below a row «Целые листы» are whole sheets of the store.

A remnant with a sketch is taken as the rectangle of its size and marked: its shape is
only a picture. A remnant kept for another order is not used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .materials import SheetMaterial, parse

_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[хxХX×*]\s*(\d+(?:[.,]\d+)?)")
DEFAULT_SHEETS = ((6000.0, 1500.0),)


@dataclass
class StockSheet:
    material: SheetMaterial
    length: float  # mm, the longer side
    width: float
    quantity: int = 1
    remnant: bool = True  # False — a whole sheet
    row: int = 0  # in the list, from 1; 0 — not from a list
    number: str = ""  # «№ п/п» of the list
    text: str = ""  # the cells as written: «8 09Г2С», «810х1360»
    size_text: str = ""
    color: str = ""
    reserved: str = ""  # «Задействованные»
    sketch: bool = False  # the remnant has a sketch: not a rectangle
    unlimited: bool = False  # a standard sheet: as many as needed

    @property
    def area(self) -> float:
        return self.length * self.width

    @property
    def size(self) -> str:
        return f"{self.length:g}×{self.width:g}"

    def describe(self) -> str:
        if not self.remnant:
            return f"Лист {self.size}"
        where = f" (строка {self.row})" if self.row else ""
        return f"Остаток {self.size}{where}"

    def free_for(self, order: str) -> bool:
        """Not kept for another order: «Задействованные» is empty or names *order*."""
        reserved = self.reserved.strip()
        return not reserved or bool(order) and order in reserved


@dataclass
class Stock:
    path: Path | None
    sheets: list[StockSheet]
    problems: list[str]
    header_row: int = 0
    columns: dict | None = None  # the columns of the list, for the file of the changes


def parse_size(text) -> tuple[float, float] | None:
    match = _SIZE_RE.search(str(text or ""))
    if not match:
        return None
    a, b = (float(g.replace(",", ".")) for g in match.groups())
    return (max(a, b), min(a, b)) if a > 0 and b > 0 else None


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _columns(row: list) -> dict | None:
    found: dict[str, int] = {}
    for i, cell in enumerate(row):
        title = _text(cell).lower()
        if not title:
            continue
        for key, words in (("material", ("толщин", "материал")), ("size", ("габарит", "размер")),
                           ("quantity", ("кол-во", "количеств", "кол.")), ("sketch", ("эскиз",)),
                           ("color", ("цвет", "маркиров")), ("reserved", ("задейств", "резерв", "заказ")),
                           ("number", ("№",)), ("grade", ("марка",))):
            if key not in found and any(w in title for w in words):
                found[key] = i
                break
    return found if "material" in found and "size" in found else None


def read_stock(path: str | Path) -> Stock:
    """The remnants and whole sheets of the list in *path*."""
    import openpyxl

    path = Path(path)
    book = openpyxl.load_workbook(str(path), data_only=True)  # not read-only: the pictures are needed
    try:
        sheet = next((s for s in book.worksheets if s.max_row > 1), book.active)
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        pictures = set()
        for image in getattr(sheet, "_images", []):
            try:
                anchor = image.anchor._from
                pictures.add((anchor.row + 1, anchor.col))
            except AttributeError:
                continue
    finally:
        book.close()
    stock = Stock(path, [], [])
    columns, header = None, 0
    for i, row in enumerate(rows[:15]):
        columns = _columns(row)
        if columns:
            header = i + 1
            break
    if columns is None:
        stock.problems.append("В списке остатков нет колонок «Толщина» и «Габариты».")
        return stock
    stock.header_row, stock.columns = header, columns
    whole = False

    def cell(row: list, key: str):
        index = columns.get(key, -1)
        return row[index] if 0 <= index < len(row) else None

    for number, row in enumerate(rows[header:], start=header + 1):
        joined = " ".join(_text(v) for v in row).lower()
        if "целые лист" in joined or "целых лист" in joined:
            whole = True
        material_text = _text(cell(row, "material"))
        size_text = _text(cell(row, "size"))
        if not material_text and not size_text:
            continue
        material = parse(material_text)
        grade = _text(cell(row, "grade"))
        if material and grade:
            material = parse(f"{material.thickness:g} {grade}{' чечевица' if material.checker else ''}")
        size = parse_size(size_text)
        if material is None or size is None:
            if material_text.lower().startswith("итого") or not (material_text and size_text):
                continue
            stock.problems.append(f"Строка {number}: «{material_text} {size_text}» — не понял толщину или размер.")
            continue
        quantity = cell(row, "quantity")
        try:
            quantity = int(float(str(quantity).replace(",", "."))) if quantity not in (None, "") else 1
        except ValueError:
            quantity = 1
        if quantity <= 0:
            continue
        sketch_col = columns.get("sketch", -1)
        stock.sheets.append(StockSheet(
            material=material, length=size[0], width=size[1], quantity=quantity, remnant=not whole,
            row=number, number=_text(cell(row, "number")), text=material_text, size_text=size_text,
            color=_text(cell(row, "color")), reserved=_text(cell(row, "reserved")),
            sketch=sketch_col >= 0 and (number, sketch_col) in pictures))
    return stock


def standard_sheets(material: SheetMaterial, sizes=DEFAULT_SHEETS) -> list[StockSheet]:
    return [StockSheet(material, max(l, w), min(l, w), remnant=False, unlimited=True) for l, w in sizes]
