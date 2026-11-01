"""«Сравнить версии сборки»: what changed between two versions of a product.

A version is an assembly (.a3d, read like the assembly report) or the snapshot of a
report: every report saves one next to its workbook («… - снимок.json»), so an old
version needs no old model — its report is enough.

The components are matched by designation, or — without one — by name and model
file. Added, removed and changed (quantity on the product, material, mass, section
of the specification, model file); the totals «было → стало»: mass, metal by
material, positions, fasteners by type and size, welds, painting, hours, and the
cost when the norms of the plant are there. Bought units on a sheet of their own,
for purchasing.
"""
from __future__ import annotations

import json
import ntpath
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .assembly import ASSEMBLY, DETAIL, KIND_TITLES, OTHER, STANDARD, material_text

SNAPSHOT_VERSION = 1
SNAPSHOT_SUFFIX = " - снимок.json"
MASS_TOLERANCE = 0.005  # 0.5 %: a remodelled detail of the same mass is not a change


# ── the snapshot of a report ─────────────────────────────────────────────────


def _item(c, qty: int) -> dict:
    item = {"kind": c.kind, "marking": c.marking, "name": c.name, "material": material_text(c.material),
            "mass_kg": round(c.mass_kg, 4), "qty": qty, "section": c.section,
            "file": ntpath.basename(c.file.split(">", 1)[-1]) if c.file else ""}
    if c.kind == DETAIL:  # for the purchase list (cad/purchase.py): sheets by thickness
        item["sheet_t"] = sheet_thickness(c)
    return item


def sheet_thickness(c) -> float:
    """The thickness of a detail cut from a sheet, mm: the sheet of its material (it is ordered by
    it, as «Пакет на лазер» takes it), else its sheet body; 0 — not from a sheet."""
    try:
        from dxfka.utils import thickness_from_material

        named = float(thickness_from_material(c.material) or 0)
    except Exception:
        named = 0.0
    return named or round(c.sheet_thickness, 3)


def snapshot(report, cost: float | None = None) -> dict:
    """What a later version is compared with: the components and the totals of *report*."""
    from . import dfma
    from .workload import paint, workload

    items = [_item(s.assembly, s.qty_product) for s in report.sections[1:]]
    for lines in (report.details, report.standard, report.other):
        items += [_item(l.component, l.qty_product) for l in lines]
    try:
        hours = sum(r.hours for r in workload(report))
    except Exception:  # an unreadable norms file
        hours = None
    fasteners = {f"{dfma.PLURAL.get(r.type, r.type)} {r.size}": r.qty for r in report.fastener_rows}
    return {
        "version": SNAPSHOT_VERSION,
        "made": datetime.now().isoformat(timespec="seconds"),
        "product": {"marking": report.top.marking, "name": report.top.name, "file": report.top.file,
                    "mass_kg": round(report.top.mass_kg, 3)},
        "items": items,
        "totals": {
            "metal": {m["material"]: round(m["mass_kg"], 3) for m in report.metal},
            "fasteners": fasteners,
            "weld_m": round(report.weld_length_m, 2) if report.welds else None,
            "paint_m2": round(paint(report).paint_m2, 2),
            "hours": round(hours, 2) if hours is not None else None,
            "cost": round(cost, 2) if cost is not None else None,
        },
    }


def snapshot_path(xlsx_path: str | Path) -> Path:
    """«… - ведомость и металлоёмкость.xlsx» -> «… - ведомость и металлоёмкость - снимок.json»."""
    path = Path(xlsx_path)
    return path.with_name(path.stem + SNAPSHOT_SUFFIX)


def save_snapshot(report, xlsx_path: str | Path, cost: float | None = None) -> Path:
    path = snapshot_path(xlsx_path)
    path.write_text(json.dumps(snapshot(report, cost), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_snapshot(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != SNAPSHOT_VERSION or "items" not in data:
        raise ValueError(f"Не снимок отчёта Fluxion: {Path(path).name}")
    return data


# ── the difference ───────────────────────────────────────────────────────────


# «792720.2801010 Изм2», «792720.2801220 Изм.2-01»: the notice of change written into the designation
_CHANGE_NOTE_RE = re.compile(r"\s*изм\.?\s*\d+", re.IGNORECASE)


def version_snapshot(path: str, log) -> dict:
    """The snapshot of a version: read from a .json, or the assembly read and reported."""
    if Path(path).suffix.lower() == ".json":
        return load_snapshot(path)
    from . import assembly, report

    top = assembly.read(path, log=log)
    result = report.build(top, report.load_decisions(top.file))
    report.add_welds(result, log=log)
    try:
        from . import estimate

        cost = estimate.build(result).total
    except Exception:  # unreadable norms: compared without the cost
        cost = None
    return snapshot(result, cost)


def base_marking(marking: str) -> str:
    """The designation without the notice of change: «792720.2801220 Изм2-01» -> «792720.2801220-01»."""
    return " ".join(_CHANGE_NOTE_RE.sub("", marking or "").split()).lower()


def key(item: dict) -> tuple:
    """Designation (without «Изм2»); without one — the name and the model file."""
    if item["marking"]:
        return (item["kind"], base_marking(item["marking"]), "")
    return (item["kind"], item["name"].strip().lower(), item["file"].lower())


def _merged(items: list[dict]) -> dict[tuple, dict]:
    """One item per key: the same detail in two subassemblies adds its quantities."""
    out: dict[tuple, dict] = {}
    for item in items:
        k = key(item)
        if k in out:
            out[k] = dict(out[k], qty=out[k]["qty"] + item["qty"])
        else:
            out[k] = dict(item)
    return out


@dataclass
class Change:
    kind: str  # «Деталь», «Стандартное изделие»…
    marking: str
    name: str
    what: str  # «Добавлено», «Удалено», «Количество», «Материал»…
    old: str = ""
    new: str = ""
    item: dict = field(default_factory=dict)

    @property
    def bought(self) -> bool:
        return self.item.get("kind") in (OTHER, STANDARD)


@dataclass
class Diff:
    old: dict
    new: dict
    added: list[Change] = field(default_factory=list)
    removed: list[Change] = field(default_factory=list)
    changed: list[Change] = field(default_factory=list)

    @property
    def changes(self) -> list[Change]:
        return self.added + self.removed + self.changed

    def totals(self) -> list[tuple[str, object, object]]:
        """(what, было, стало) of the totals both versions have."""
        o, n = self.old["totals"], self.new["totals"]
        rows = [("Масса изделия, кг", self.old["product"]["mass_kg"], self.new["product"]["mass_kg"])]
        count = lambda snap, kinds: sum(1 for i in snap["items"] if i["kind"] in kinds)  # noqa: E731
        for title, kinds in (("Позиций деталей", (DETAIL,)), ("Сборочных единиц", (ASSEMBLY,)),
                             ("Стандартных изделий, поз.", (STANDARD,)), ("Покупных (прочих) изделий, поз.", (OTHER,))):
            rows.append((title, count(self.old, kinds), count(self.new, kinds)))
        for k, title in (("weld_m", "Швы, м"), ("paint_m2", "Покраска, м²"), ("hours", "Трудоёмкость модели, ч"),
                         ("cost", "Себестоимость, ₽")):
            if o.get(k) is not None or n.get(k) is not None:
                rows.append((title, o.get(k), n.get(k)))
        return rows

    def metal(self) -> list[tuple[str, float, float]]:
        o, n = self.old["totals"]["metal"], self.new["totals"]["metal"]
        names = list(dict.fromkeys(list(n) + list(o)))
        return [(m, o.get(m, 0.0), n.get(m, 0.0)) for m in names if abs(o.get(m, 0.0) - n.get(m, 0.0)) > 1e-6]

    def fasteners(self) -> list[tuple[str, int, int]]:
        o, n = self.old["totals"]["fasteners"], self.new["totals"]["fasteners"]
        names = sorted(set(o) | set(n))
        return [(f, o.get(f, 0), n.get(f, 0)) for f in names if o.get(f, 0) != n.get(f, 0)]


def _fmt_mass(value: float) -> str:
    """«6 410.5 кг», «2.004 кг», «50 кг»."""
    digits = 1 if value >= 100 else 3
    text = f"{value:,.{digits}f}".replace(",", " ").rstrip("0").rstrip(".")
    return text + " кг"


def compare(old: dict, new: dict) -> Diff:
    """What changed from the snapshot *old* to the snapshot *new*."""
    a, b = _merged(old["items"]), _merged(new["items"])
    diff = Diff(old, new)

    def change(item, what, was="", now=""):
        return Change(KIND_TITLES.get(item["kind"], item["kind"]), item["marking"], item["name"], what, str(was),
                      str(now), item)

    for k, item in b.items():
        if k not in a:
            diff.added.append(change(item, "Добавлено", "", f"{item['qty']} шт."))
    for k, item in a.items():
        if k not in b:
            diff.removed.append(change(item, "Удалено", f"{item['qty']} шт.", ""))
    for k, new_item in b.items():
        old_item = a.get(k)
        if old_item is None:
            continue
        if old_item["marking"].strip() != new_item["marking"].strip():
            diff.changed.append(change(new_item, "Обозначение", old_item["marking"], new_item["marking"]))
        if old_item["qty"] != new_item["qty"]:
            diff.changed.append(change(new_item, "Количество на изделие", old_item["qty"], new_item["qty"]))
        if old_item["material"] != new_item["material"]:
            diff.changed.append(change(new_item, "Материал", old_item["material"] or "—", new_item["material"] or "—"))
        mass_old, mass_new = old_item["mass_kg"], new_item["mass_kg"]
        if abs(mass_new - mass_old) > MASS_TOLERANCE * max(mass_old, mass_new, 1e-9):
            diff.changed.append(change(new_item, "Масса", _fmt_mass(mass_old), _fmt_mass(mass_new)))
        if old_item["section"] != new_item["section"]:
            diff.changed.append(change(new_item, "Раздел спецификации", old_item["section"] or "—",
                                       new_item["section"] or "—"))
        renamed = old_item["marking"].strip() != new_item["marking"].strip()  # its file is renamed with it
        if new_item["marking"] and not renamed and old_item["file"] and new_item["file"] and \
                old_item["file"].lower() != new_item["file"].lower():
            diff.changed.append(change(new_item, "Файл модели", old_item["file"], new_item["file"]))
    order = {ASSEMBLY: 0, DETAIL: 1, STANDARD: 2, OTHER: 3}
    for changes in (diff.added, diff.removed, diff.changed):
        changes.sort(key=lambda c: (order.get(c.item.get("kind"), 9), c.marking, c.name))
    return diff


def _signed(value: float, unit: str = "", digits: int = 1) -> str:
    text = f"{value:+,.{digits}f}".replace(",", " ")
    return text + (f" {unit}" if unit else "")


def summary_text(diff: Diff) -> str:
    title = " ".join(x for x in (diff.new["product"]["marking"], diff.new["product"]["name"]) if x)
    if not diff.changes:
        return f"{title}: изменений нет — состав, материалы и массы те же."
    parts = [f"+{len(diff.added)} поз.", f"−{len(diff.removed)} поз.", f"изменено {len(diff.changed)}"]
    mass = diff.new["product"]["mass_kg"] - diff.old["product"]["mass_kg"]
    text = f"{title}: " + ", ".join(parts) + f"; масса {_signed(mass, 'кг')}"
    o, n = diff.old["totals"].get("cost"), diff.new["totals"].get("cost")
    if o is not None and n is not None:
        text += f"; себестоимость {_signed(n - o, '₽', 0)}"
    bought = [c for c in diff.changes if c.bought]
    if bought:
        text += f"; покупные и стандартные — {len(bought)} изменений (лист «Покупные»)"
    return text + "."


# ── Excel ────────────────────────────────────────────────────────────────────


def default_path(diff: Diff, folder: Path | None = None) -> Path:
    from .kompas import default_save_dir

    base = Path(folder) if folder else default_save_dir()
    stem = diff.new["product"]["marking"] or diff.new["product"]["name"] or "Сборка"
    for bad in '<>:"/\\|?*':
        stem = stem.replace(bad, "_")
    path = base / f"{stem} - изменения.xlsx"
    for n in range(2, 100):  # an earlier comparison may be open in Excel
        if not path.exists():
            break
        path = base / f"{stem} - изменения ({n}).xlsx"
    return path


def _source(snap: dict) -> str:
    product = snap["product"]
    return f"{product.get('file') or product.get('name')} ({snap.get('made', '')[:16].replace('T', ' ')})"


def save_xlsx(diff: Diff, path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="DDE4EE")
    fills = {"Добавлено": PatternFill("solid", fgColor="E3F4E1"), "Удалено": PatternFill("solid", fgColor="FBE3E3")}
    wrap = Alignment(wrap_text=True, vertical="top")
    wb = Workbook()

    def sheet(title, headers, widths, first=False):
        ws = wb.active if first else wb.create_sheet(title)
        ws.title = title
        ws.append([f"Было: {_source(diff.old)}"])
        ws.append([f"Стало: {_source(diff.new)}"])
        ws.append([])
        ws.append(headers)
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = bold, head
        for col, width in zip("ABCDEFGH", widths):
            ws.column_dimensions[col].width = width
        return ws

    ws = sheet("Итоги", ["Показатель", "Было", "Стало", "Разница"], [36, 16, 16, 16], first=True)
    for title, old, new in diff.totals():
        delta = (new - old) if isinstance(old, (int, float)) and isinstance(new, (int, float)) else None
        ws.append([title, old, new, delta])
    ws.append([])
    ws.append(["Позиций: добавлено", len(diff.added)])
    ws.append(["Позиций: удалено", len(diff.removed)])
    ws.append(["Изменений у оставшихся", len(diff.changed)])
    metal = diff.metal()
    if metal:
        ws.append([])
        ws.append(["Металлоёмкость по материалам, кг", "Было", "Стало", "Разница"])
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = bold, head
        for name, old, new in metal:
            ws.append([name, old, new, new - old])
    fasteners = diff.fasteners()
    if fasteners:
        ws.append([])
        ws.append(["Крепёж по типоразмерам, шт.", "Было", "Стало", "Разница"])
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = bold, head
        for name, old, new in fasteners:
            ws.append([name, old, new, new - old])
    for row in ws.iter_rows(min_row=5):
        for cell in row[1:4]:
            if isinstance(cell.value, float):
                cell.number_format = "#,##0.0##"

    def changes_sheet(title, changes):
        ws = sheet(title, ["Изменение", "Вид", "Обозначение", "Наименование", "Было", "Стало"],
                   [24, 20, 24, 50, 30, 30])
        for c in changes:
            ws.append([c.what, c.kind, c.marking, c.name, c.old, c.new])
            fill = fills.get(c.what)
            if fill is not None:
                for cell in ws[ws.max_row]:
                    cell.fill = fill
            for cell in ws[ws.max_row][3:6]:
                cell.alignment = wrap
        if not changes:
            ws.append(["Изменений нет"])

    changes_sheet("Изменения", diff.changes)
    changes_sheet("Покупные", [c for c in diff.changes if c.bought])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
