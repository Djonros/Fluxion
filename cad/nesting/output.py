"""What a nesting gives: a DXF and a picture of every sheet, the report in Excel and the
changes of the list of remnants.

The folder: <out>\\Лист 8 09Г2С - 01.dxf / .png … , «Отчёт раскроя.xlsx»,
«Остатки — изменения.xlsx». The list of remnants itself is not written: its photos
would be lost by a rewrite.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .engine import NestedSheet, Result
from .materials import density
from .orders import Order
from .parts import Part

LAYER_SHEET, LAYER_CUT, LAYER_TEXT, LAYER_REMNANT = "ЛИСТ", "РЕЗ", "МАРКИРОВКА", "ОСТАТОК"
REPORT_NAME = "Отчёт раскроя.xlsx"
CHANGES_NAME = "Остатки — изменения.xlsx"


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", text).strip(" .") or "раскрой"


def sheet_name(group_label: str, number: int) -> str:
    return safe_name(f"Лист {group_label} - {number:02d}")


def part_numbers(result: Result) -> dict[int, int]:
    """id(part) → its number on the pictures, in the order of the report."""
    numbers: dict[int, int] = {}
    for group in result.groups:
        for sheet in group.sheets:
            for placement in sheet.placements:
                numbers.setdefault(id(placement.part), len(numbers) + 1)
    return numbers


# ── DXF ──────────────────────────────────────────────────────────────────────


def write_dxf(sheet: NestedSheet, path: Path, numbers: dict[int, int]) -> Path:
    """The sheet: its border, the contours of the details (layer РЕЗ), their designations
    (МАРКИРОВКА, not cut) and the cut-off line of the new remnant (ОСТАТОК)."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # mm
    for name, color in ((LAYER_SHEET, 8), (LAYER_CUT, 7), (LAYER_TEXT, 3), (LAYER_REMNANT, 1)):
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    L, W = sheet.length, sheet.width
    msp.add_lwpolyline([(0, 0), (L, 0), (L, W), (0, W)], close=True, dxfattribs={"layer": LAYER_SHEET})
    for p in sheet.placements:
        for loop in p.loops:
            msp.add_lwpolyline([(float(x), float(y)) for x, y in loop], close=True, dxfattribs={"layer": LAYER_CUT})
        cx, cy = p.center
        height = max(5.0, min(30.0, (p.box[3] - p.box[1]) / 6))
        msp.add_text(f"{numbers.get(id(p.part), '')} {p.part.item.designation}",
                     dxfattribs={"layer": LAYER_TEXT, "height": height}).set_placement(
            (cx, cy), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    if sheet.remnant:
        msp.add_line((sheet.cut_x, 0), (sheet.cut_x, W), dxfattribs={"layer": LAYER_REMNANT, "linetype": "DASHED"})
        msp.add_text(f"Деловой остаток {sheet.remnant.size}", dxfattribs={"layer": LAYER_REMNANT, "height": 30}
                     ).set_placement(((sheet.cut_x + L) / 2, W / 2),
                                     align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(str(path))
    return path


# ── picture ──────────────────────────────────────────────────────────────────


def render_png(sheet: NestedSheet, path: Path | None, numbers: dict[int, int], width_px: int = 1600,
               title: str = "", cuts=None):
    """A picture of the sheet: details with their numbers, the new remnant hatched. Saved to
    *path* when given; the PIL image is returned."""
    from PIL import Image, ImageDraw, ImageFont

    L, W = sheet.length, sheet.width
    pad, head = 20, 36 if title else 0
    scale = (width_px - 2 * pad) / L
    h_px = int(W * scale) + 2 * pad + head
    image = Image.new("RGB", (width_px, h_px), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
        big = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 14)
            big = ImageFont.truetype("DejaVuSans.ttf", 18)
        except OSError:
            font = big = ImageFont.load_default()

    def xy(x: float, y: float) -> tuple[float, float]:
        return pad + x * scale, head + pad + (W - y) * scale

    if title:
        draw.text((pad, 8), title, fill="#222222", font=big)
    draw.rectangle([xy(0, W), xy(L, 0)], fill="#d9dde3", outline="#555555", width=2)
    if sheet.remnant:
        x0, y0 = xy(sheet.cut_x, W)
        x1, y1 = xy(L, 0)
        draw.rectangle([x0, y0, x1, y1], fill="#e9f5e1")
        step = 14
        for k in range(int(x0) - int(y1 - y0), int(x1), step):
            a = (max(k, x0), y1 - max(0, max(k, x0) - k))
            b = (min(k + (y1 - y0), x1), y1 - (min(k + (y1 - y0), x1) - k))
            draw.line([a, b], fill="#b9dca8")
        draw.line([xy(sheet.cut_x, 0), xy(sheet.cut_x, W)], fill="#3b8a2a", width=2)
        cx, cy = xy((sheet.cut_x + L) / 2, W / 2)
        draw.text((cx, cy), f"остаток {sheet.remnant.size}", fill="#2d6b20", font=font, anchor="mm")
    for p in sheet.placements:
        outer, holes = p.loops[0], p.loops[1:]
        draw.polygon([xy(float(x), float(y)) for x, y in outer], fill="#7fa7d9", outline="#1f4f8f")
        for hole in holes:
            draw.polygon([xy(float(x), float(y)) for x, y in hole], fill="#d9dde3", outline="#1f4f8f")
        cx, cy = xy(*p.center)
        draw.text((cx, cy), str(numbers.get(id(p.part), "")), fill="#0b2545", font=font, anchor="mm")
    if cuts is not None:  # the piercings and lead-ins of the program, red
        for cut in cuts.cuts:
            sx, sy = xy(*cut.start)
            if cut.moves and cut.lead_length > 0:
                draw.line([(sx, sy), xy(*cut.moves[0].end)], fill="#d32f2f", width=2)
            draw.ellipse([sx - 2.5, sy - 2.5, sx + 2.5, sy + 2.5], fill="#d32f2f")
    if path is not None:
        image.save(str(path))
    return image


# ── report ───────────────────────────────────────────────────────────────────


def _mass(area_mm2: float, sheet: NestedSheet) -> float:
    return area_mm2 * sheet.stock.material.thickness * density(sheet.stock.material) / 1e6


def write_report(order: Order, parts: list[Part], result: Result, folder: Path,
                 pictures: dict[int, Path] | None = None, stock_path: Path | None = None,
                 programs: dict[int, dict] | None = None) -> Path:
    """*programs* — id(sheet) → {"file", "minutes", "cut", "pierces"} of its program."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    numbers = part_numbers(result)
    book = Workbook()
    bold, head_fill = Font(bold=True), PatternFill("solid", fgColor="DDE6F2")

    def header(sheet, titles, widths):
        sheet.append(titles)
        for i, (cell, width) in enumerate(zip(sheet[sheet.max_row], widths), start=1):
            cell.font, cell.fill = bold, head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            sheet.column_dimensions[cell.column_letter].width = width
        sheet.freeze_panes = sheet.cell(row=sheet.max_row + 1, column=1)

    summary = book.active
    summary.title = "Листы"
    summary.append([f"Раскрой {order.number or order.path.stem}: изделий {order.products}, "
                    f"{datetime.now():%d.%m.%Y %H:%M}"])
    summary["A1"].font = Font(bold=True, size=13)
    settings = result.settings
    summary.append([f"Зазор между деталями {settings.gap:g} мм, отступ от края {settings.margin:g} мм, "
                    f"повороты {', '.join(f'{a:g}°' for a in settings.rotations)}"])
    summary.append([])
    header(summary, ["Материал", "№ листа", "Откуда", "Размер, мм", "Деталей", "Использование, %",
                     "Масса листа, кг", "Масса деталей, кг", "Отход, кг", "Длина реза, м", "Прожигов",
                     "Новый деловой остаток", "Файл", "Программа", "Время резки, мин"],
           [16, 8, 30, 14, 9, 14, 12, 14, 10, 12, 10, 22, 30, 30, 12])
    programs = programs or {}
    total = {"sheet": 0.0, "parts": 0.0, "cut": 0.0, "pierces": 0, "minutes": 0.0}
    for group in result.groups:
        for n, sheet in enumerate(group.sheets, start=1):
            sheet_mass = _mass(sheet.used_area, sheet)
            parts_mass = _mass(sheet.parts_area, sheet)
            total["sheet"] += sheet_mass
            total["parts"] += parts_mass
            prog = programs.get(id(sheet), {})
            cut_m = prog.get("cut", sheet.cut_length) / 1000
            pierces = prog.get("pierces", sheet.pierces)
            total["cut"] += cut_m
            total["pierces"] += pierces
            total["minutes"] += prog.get("minutes", 0.0)
            where = sheet.stock.describe()
            if sheet.stock.sketch:
                where += " — есть эскиз, проверьте форму"
            summary.append([group.material.label(), n, where, sheet.stock.size, len(sheet.placements),
                            round(sheet.utilization * 100, 1), round(sheet_mass, 1), round(parts_mass, 1),
                            round(sheet_mass - parts_mass, 1), round(cut_m, 2), pierces,
                            sheet.remnant.size if sheet.remnant else "",
                            sheet_name(group.material.label(), n) + ".dxf",
                            Path(prog["file"]).name if prog.get("file") else "",
                            round(prog["minutes"], 1) if "minutes" in prog else ""])
    summary.append([])
    summary.append(["Итого", "", "", "", "", "", round(total["sheet"], 1), round(total["parts"], 1),
                    round(total["sheet"] - total["parts"], 1), round(total["cut"], 2), total["pierces"], "", "",
                    round(total["minutes"], 1) if programs else ""])
    for cell in summary[summary.max_row]:
        cell.font = bold

    details = book.create_sheet("Детали")
    header(details, ["№ на карте", "Обозначение", "Наименование", "Материал", "Нужно", "Разложено",
                     "На листах", "DXF", "Габарит, мм", "Площадь, м²", "Замечание"],
           [10, 22, 34, 18, 8, 10, 16, 50, 14, 12, 40])
    on_sheets: dict[int, list[str]] = {}
    placed: dict[int, int] = {}
    for group in result.groups:
        for n, sheet in enumerate(group.sheets, start=1):
            for p in sheet.placements:
                placed[id(p.part)] = placed.get(id(p.part), 0) + 1
                label = f"{n}"
                if label not in on_sheets.setdefault(id(p.part), []):
                    on_sheets[id(p.part)].append(label)
    too_big = {id(p) for g in result.groups for p in g.too_big}
    for part in parts:
        item = part.item
        notes = []
        if part.error:
            notes.append(part.error)
        if id(part) in too_big:
            notes.append("не помещается ни на один лист")
        left = item.quantity - placed.get(id(part), 0)
        if part.shape is not None and left > 0 and id(part) not in too_big and item.material is not None:
            notes.append(f"не разложено {left} шт.")
        if item.material is None:
            notes.append("не понял толщину")
        if item.bent:
            notes.append("гнутая: DXF должен быть развёрткой")
        if len(part.candidates) > 1:
            notes.append(f"найдено файлов: {len(part.candidates)}, взят первый")
        shape = part.shape
        details.append([numbers.get(id(part), ""), item.designation, item.name,
                        item.material.label() if item.material else item.material_text, item.quantity,
                        placed.get(id(part), 0), ", ".join(on_sheets.get(id(part), [])),
                        str(part.path) if part.path else "",
                        f"{shape.length:.0f}×{shape.width:.0f}" if shape else "",
                        round(shape.area / 1e6, 4) if shape else "", "; ".join(notes)])
        if notes and (part.error or left > 0 or item.material is None):
            for cell in details[details.max_row]:
                cell.fill = PatternFill("solid", fgColor="FBE3E3")

    remnants = book.create_sheet("Остатки")
    header(remnants, ["Что", "Материал", "Размер, мм", "Строка в списке", "№ п/п", "Цвет", "Лист раскроя"],
           [16, 18, 14, 14, 8, 12, 20])
    for group in result.groups:
        for n, sheet in enumerate(group.sheets, start=1):
            if sheet.stock.remnant and sheet.stock.row:
                remnants.append(["Взят", sheet.stock.material.label(), sheet.stock.size, sheet.stock.row,
                                 sheet.stock.number, sheet.stock.color, n])
            if sheet.remnant:
                remnants.append(["Новый", group.material.label(), sheet.remnant.size, "", "", "", n])

    if order.problems:
        problems = book.create_sheet("Замечания")
        header(problems, ["Заявка"], [100])
        for text in order.problems:
            problems.append([text])

    if pictures:
        from openpyxl.drawing.image import Image as XlImage

        maps = book.create_sheet("Карты раскроя")
        row = 1
        for png in pictures.values():
            try:
                picture = XlImage(str(png))
            except Exception:  # PIL missing or a broken file: the map is in the folder anyway
                continue
            ratio = 900 / picture.width
            picture.width, picture.height = 900, int(picture.height * ratio)
            maps.add_image(picture, f"A{row}")
            row += int(picture.height / 20) + 3
    path = folder / REPORT_NAME
    book.save(str(path))
    return path


def write_changes(order: Order, result: Result, folder: Path, stock_path: Path | None) -> Path | None:
    """What to change in the list of remnants: the remnants taken (by their row) and the new
    ones in the form of the list («8 09Г2С», «1500х782»)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    taken: dict[int, list] = {}
    new = []
    for group in result.groups:
        for n, sheet in enumerate(group.sheets, start=1):
            if sheet.stock.remnant and sheet.stock.row:
                entry = taken.setdefault(id(sheet.stock), [sheet.stock, 0])
                entry[1] += 1
            if sheet.remnant:
                new.append((group.material, sheet.remnant, n))
    if not taken and not new:
        return None
    book = Workbook()
    sheet = book.active
    sheet.title = "Изменения"
    source = f" «{stock_path.name}»" if stock_path else ""
    sheet.append([f"Изменения списка деловых остатков{source} после раскроя {order.number or order.path.stem} "
                  f"от {datetime.now():%d.%m.%Y}"])
    sheet["A1"].font = Font(bold=True, size=12)
    sheet.append([])
    if taken:
        sheet.append(["Списать (взяты в раскрой)"])
        sheet[sheet.max_row][0].font = Font(bold=True)
        sheet.append(["Строка", "№ п/п", "Толщина листа мм", "Габариты мм", "Было", "Взято", "Осталось", "Цвет"])
        for stock_sheet, count in sorted(taken.values(), key=lambda e: e[0].row):
            sheet.append([stock_sheet.row, stock_sheet.number, stock_sheet.text, stock_sheet.size_text,
                          stock_sheet.quantity, count, stock_sheet.quantity - count, stock_sheet.color])
        sheet.append([])
    if new:
        sheet.append(["Добавить (новые деловые остатки)"])
        sheet[sheet.max_row][0].font = Font(bold=True)
        sheet.append(["Толщина листа мм", "Габариты мм", "Кол-во", "Задействованные", "Из листа раскроя"])
        merged: dict[tuple, int] = {}
        for material, remnant, n in new:
            key = (material.label(), f"{remnant.length:.0f}х{remnant.width:.0f}")
            merged[key] = merged.get(key, 0) + 1
        for (label, size), count in merged.items():
            sheets_of = ", ".join(str(n) for m, r, n in new
                                  if m.label() == label and f"{r.length:.0f}х{r.width:.0f}" == size)
            sheet.append([label, size, count, "", sheets_of])
    for column, width in zip("ABCDEFGH", (18, 14, 18, 14, 10, 10, 10, 12)):
        sheet.column_dimensions[column].width = width
    path = folder / CHANGES_NAME
    book.save(str(path))
    return path


def save_all(order: Order, parts: list[Part], result: Result, folder: str | Path,
             stock_path: str | Path | None = None, machine=None, cut=None) -> dict:
    """DXF and PNG of every sheet, the report, the changes of the remnants and, with a
    *machine* (post.Machine), the program of every sheet. The paths."""
    from dataclasses import replace

    from . import cutting, post

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    numbers = part_numbers(result)
    dxf, png, programs, files = [], {}, {}, []
    for group in result.groups:
        for n, sheet in enumerate(group.sheets, start=1):
            name = sheet_name(group.material.label(), n)
            dxf.append(write_dxf(sheet, folder / f"{name}.dxf", numbers))
            path = None
            if machine is not None:
                tech = machine.technology_for(group.material.thickness)
                given = cut or cutting.CutSettings()
                settings = replace(given, kerf=given.kerf or tech["kerf"], feed=given.feed or tech["feed"],
                                   lead_in=given.lead_in or tech["lead_in"], overcut=given.overcut or tech["overcut"],
                                   pierce_time=tech["pierce"])
                sheet_machine = replace(machine, thc_off_before_end=tech["hold"]) if tech["hold"] else machine
                path = cutting.sheet_path(sheet, settings)
                text = post.program(path, sheet_machine, settings.feed, f"{order.number} {name}")
                program_file = post.save(text, folder / f"{name}{machine.extension}", machine)
                files.append(program_file)
                programs[id(sheet)] = {"file": program_file, "cut": path.cut_length, "pierces": path.pierces,
                                       "minutes": path.seconds(settings.feed, settings) / 60,
                                       "skipped": path.skipped_holes}
            title = (f"{group.material.label()} — лист {n}: {sheet.stock.describe()}, деталей {len(sheet.placements)}, "
                     f"использование {sheet.utilization:.0%}")
            render_png(sheet, folder / f"{name}.png", numbers, title=title, cuts=path)
            png[len(png)] = folder / f"{name}.png"
    stock_path = Path(stock_path) if stock_path else None
    report = write_report(order, parts, result, folder, png, stock_path, programs)
    changes = write_changes(order, result, folder, stock_path)
    return {"folder": folder, "dxf": dxf, "png": list(png.values()), "report": report, "changes": changes,
            "programs": files, "minutes": sum(p["minutes"] for p in programs.values())}


def summary_text(order: Order, parts: list[Part], result: Result, minutes: float = 0.0) -> str:
    """A few lines for the chat."""
    lines = []
    sheets = sum(len(g.sheets) for g in result.groups)
    placed = sum(len(s.placements) for g in result.groups for s in g.sheets)
    lines.append(f"Раскрой {order.number or order.path.stem}: {placed} деталей на {sheets} листах "
                 f"за {result.seconds:.0f} с.")
    for group in result.groups:
        remnants = sum(1 for s in group.sheets if s.stock.remnant)
        whole = len(group.sheets) - remnants
        used = sum(s.parts_area for s in group.sheets) / max(1.0, sum(s.used_area for s in group.sheets))
        new = sum(1 for s in group.sheets if s.remnant)
        bits = [f"листов {whole}"] + ([f"остатков взято {remnants}"] if remnants else []) + \
               ([f"новых остатков {new}"] if new else [])
        lines.append(f"• {group.material.label()}: {', '.join(bits)}, использование {used:.0%}.")
    if minutes:
        lines.append(f"Программы для станка: время резки около {minutes:.0f} мин.")
    missing = [p for p in parts if p.error]
    if missing:
        lines.append(f"Без DXF или с ошибкой: {len(missing)} — " +
                     ", ".join(p.item.designation for p in missing[:8]) + (" …" if len(missing) > 8 else ""))
    left = sum(sum(g.unplaced.values()) for g in result.groups)
    big = sum(len(g.too_big) for g in result.groups)
    if left or big:
        lines.append(f"Не разложено: {left} шт., не помещаются на лист: {big} поз.")
    return "\n".join(lines)


def default_folder(order: Order, base: str | Path | None = None) -> Path:
    """<base or Документы\\Fluxion CAD\\Раскрой>\\<order> <date time>."""
    from cad.kompas import default_save_dir

    base = Path(base) if base else default_save_dir() / "Раскрой"
    name = safe_name(f"{order.number or order.path.stem} {datetime.now():%Y-%m-%d %H-%M}")
    return base / name

