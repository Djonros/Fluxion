"""«Заявка на закупку»: what to buy for several products, each in its quantity.

The products are snapshots of their assembly reports (cad/compare.py: an assembly
is read into one, a saved «… - снимок.json» is taken as is). Summed over the order:

- Sheets by material and thickness: the area of the details (mass ÷ density ÷
  thickness), the waste of the plant's norms, the sheets of the standard format
  (rounded up) and the mass.
- Rolled metal and tubes by sortament: the mass with waste and the pieces. No
  length in metres: there are no linear masses here, and a guess is not written.
- Fasteners and standard items, bought units: pieces; the price and the sum when
  the plant's norms have it, and an empty «Поставщик» for purchasing.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from . import estimate
from .assembly import DETAIL, OTHER, STANDARD, no_material

STEEL_DENSITY = 7.85  # g/cm³ when the material is not in the catalog
NO_MATERIAL = "Материал не задан (у деталей «Сталь 10» КОМПАС по умолчанию) — уточните"


@dataclass
class SheetRow:
    material: str
    thickness: float
    area_m2: float  # net, the details
    waste_pct: float
    sheet: tuple[float, float]  # the format, mm
    mass_kg: float  # with waste
    pieces: int

    @property
    def gross_m2(self) -> float:
        return self.area_m2 * (1 + self.waste_pct / 100)

    @property
    def sheets(self) -> int:
        one = self.sheet[0] * self.sheet[1] / 1e6
        return math.ceil(self.gross_m2 / one - 1e-9) if one > 0 else 0


@dataclass
class MassRow:
    material: str
    mass_kg: float  # with waste
    waste_pct: float
    pieces: int


@dataclass
class ItemRow:
    name: str
    marking: str
    qty: int
    price: float | None = None

    @property
    def total(self) -> float | None:
        return None if self.price is None else self.price * self.qty


@dataclass
class Order:
    products: list[tuple[str, int]] = field(default_factory=list)  # (title, quantity)
    sheets: list[SheetRow] = field(default_factory=list)
    rolled: list[MassRow] = field(default_factory=list)
    hardware: list[ItemRow] = field(default_factory=list)
    bought: list[ItemRow] = field(default_factory=list)

    @property
    def sheet_mass_kg(self) -> float:
        return sum(r.mass_kg for r in self.sheets)

    @property
    def rolled_mass_kg(self) -> float:
        return sum(r.mass_kg for r in self.rolled)


def density(material: str) -> float:
    """g/cm³ of *material* by the materials catalog; steel when it is not there."""
    try:
        from . import materials

        found = materials.find(material, kinds=("sheet",))
        if found is not None and found.density > 0:
            return found.density
    except Exception:
        pass
    return STEEL_DENSITY


def sheet_format(thickness: float, norms: dict) -> tuple[float, float]:
    for f in norms["sheet_formats"]:
        if not f["max_thickness"] or thickness <= f["max_thickness"] + 1e-9:
            return f["width"], f["length"]
    return 0.0, 0.0


def build(products: list[tuple[dict, int]], norms: dict | None = None) -> Order:
    """*products*: (snapshot, quantity) — the order of all of them."""
    norms = estimate.normalize(norms) if norms is not None else estimate.load_norms()
    order = Order()
    sheets: OrderedDict[tuple, dict] = OrderedDict()
    rolled: OrderedDict[str, dict] = OrderedDict()
    hardware: OrderedDict[str, ItemRow] = OrderedDict()
    bought: OrderedDict[str, ItemRow] = OrderedDict()
    for snap, n in products:
        product = snap["product"]
        order.products.append((" ".join(x for x in (product.get("marking"), product.get("name")) if x), n))
        for item in snap["items"]:
            qty = item["qty"] * n
            if item["kind"] == DETAIL:
                material = NO_MATERIAL if no_material(item["material"]) else item["material"]
                t = float(item.get("sheet_t") or 0)
                if t > 0:
                    row = sheets.setdefault((material, t), {"mass": 0.0, "pieces": 0})
                else:
                    row = rolled.setdefault(material, {"mass": 0.0, "pieces": 0})
                row["mass"] += item["mass_kg"] * qty
                row["pieces"] += qty
            elif item["kind"] == STANDARD:
                hardware.setdefault(item["name"], ItemRow(item["name"], item["marking"], 0,
                                                          estimate.item_price(item["name"], norms))).qty += qty
            elif item["kind"] == OTHER:
                key = item["marking"] or item["name"]
                price = estimate.item_price(item["name"], norms)
                if price is None and item["marking"]:
                    price = estimate.item_price(item["marking"], norms)
                bought.setdefault(key, ItemRow(item["name"], item["marking"], 0, price)).qty += qty

    def waste(material: str) -> float:
        rule = estimate.metal_rule(material, norms)
        return rule["waste_pct"] if rule else 0.0

    for (material, t), row in sheets.items():
        area = row["mass"] / (density(material) * 1000 * t / 1000) if t else 0.0  # kg ÷ (kg/m³ × m)
        w = waste(material)
        order.sheets.append(SheetRow(material, t, area, w, sheet_format(t, norms), row["mass"] * (1 + w / 100),
                                     row["pieces"]))
    for material, row in rolled.items():
        w = waste(material)
        order.rolled.append(MassRow(material, row["mass"] * (1 + w / 100), w, row["pieces"]))
    order.sheets.sort(key=lambda r: (r.material, r.thickness))
    order.rolled.sort(key=lambda r: -r.mass_kg)
    order.hardware = sorted(hardware.values(), key=lambda r: r.name)
    order.bought = sorted(bought.values(), key=lambda r: (r.marking or r.name))
    return order


def summary_text(order: Order) -> str:
    def t(kg):
        return f"{kg / 1000:,.2f} т".replace(",", " ")

    products = ", ".join(f"{title} × {n}" for title, n in order.products)
    parts = [f"листы — {t(order.sheet_mass_kg)} ({sum(r.sheets for r in order.sheets)} листов)",
             f"прокат и трубы — {t(order.rolled_mass_kg)}",
             f"крепёж и стандартные — {sum(r.qty for r in order.hardware)} шт.",
             f"покупных — {len(order.bought)} поз."]
    return f"Заявка на закупку ({products}): " + ", ".join(parts).rstrip(".") + "."


# ── Excel ────────────────────────────────────────────────────────────────────


def default_path(folder: Path | None = None) -> Path:
    from .kompas import default_save_dir

    base = Path(folder) if folder else default_save_dir()
    path = base / "Заявка на закупку.xlsx"
    for n in range(2, 100):  # an earlier one may be open in Excel
        if not path.exists():
            break
        path = base / f"Заявка на закупку ({n}).xlsx"
    return path


def save_xlsx(order: Order, path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="DDE4EE")
    wb = Workbook()

    def sheet(title, headers, widths, first=False):
        ws = wb.active if first else wb.create_sheet(title)
        ws.title = title
        ws.append(["Заявка на: " + ", ".join(f"{t} × {n}" for t, n in order.products)])
        ws.append([])
        ws.append(headers)
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = bold, head
        for col, width in zip("ABCDEFGHI", widths):
            ws.column_dimensions[col].width = width
        return ws

    ws = sheet("Листы", ["Материал", "Толщина, мм", "Площадь деталей, м²", "Отход, %", "Площадь с отходом, м²",
                         "Формат листа, мм", "Листов", "Масса с отходом, кг", "Деталей, шт.", "Поставщик"],
               [58, 12, 18, 10, 20, 16, 10, 18, 12], first=True)
    for r in order.sheets:
        fmt = f"{r.sheet[0]:g}×{r.sheet[1]:g}" if r.sheet[0] else "—"
        ws.append([r.material, r.thickness, r.area_m2, r.waste_pct, r.gross_m2, fmt, r.sheets, r.mass_kg, r.pieces])
    ws.append(["Итого", "", sum(r.area_m2 for r in order.sheets), "", sum(r.gross_m2 for r in order.sheets), "",
               sum(r.sheets for r in order.sheets), order.sheet_mass_kg, sum(r.pieces for r in order.sheets)])
    ws[ws.max_row][0].font = bold
    for row in ws.iter_rows(min_row=4):
        for i in (2, 4, 7):
            row[i].number_format = "#,##0.00"

    ws = sheet("Прокат и трубы", ["Сортамент / материал", "Масса с отходом, кг", "Отход, %", "Деталей, шт.",
                                  "Поставщик"], [70, 20, 10, 12, 30])
    for r in order.rolled:
        ws.append([r.material, r.mass_kg, r.waste_pct, r.pieces])
    ws.append(["Итого", order.rolled_mass_kg, "", sum(r.pieces for r in order.rolled)])
    ws[ws.max_row][0].font = bold
    ws.append([])
    ws.append(["Длина в метрах не указана: в Fluxion нет таблиц погонной массы проката."])
    for row in ws.iter_rows(min_row=4):
        row[1].number_format = "#,##0.0"

    for title, rows in (("Крепёж и стандартные", order.hardware), ("Покупные", order.bought)):
        ws = sheet(title, ["Обозначение", "Наименование", "Кол., шт.", "Цена, ₽", "Сумма, ₽", "Поставщик"],
                   [24, 60, 10, 12, 14, 30])
        for r in rows:
            ws.append([r.marking, r.name, r.qty, r.price, r.total])
        priced = [r.total for r in rows if r.total is not None]
        ws.append(["", "Итого", sum(r.qty for r in rows), "", sum(priced) if priced else None])
        ws[ws.max_row][1].font = bold
        if len(priced) < len(rows):
            ws.append(["", "Без цены — впишите в «Нормы и цены завода» → «Покупные и крепёж»"])

    ws = sheet("Состав заявки", ["Изделие", "Количество, шт."], [70, 16])
    for title, n in order.products:
        ws.append([title, n])

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
