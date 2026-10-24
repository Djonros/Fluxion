"""Sheets of a drawing and their title blocks (ГОСТ 2.104 for ЕСКД, ГОСТ Р 21.101 for СПДС).

A sheet is a frame of a standard format (А4…А0 and the multiples of ГОСТ 2.301):
a closed polyline or a block whose box has the size of the format times the
scale it is drawn at, or, without one, all the objects of a sheet (layout)
together. The title block (штамп) is read either from a block with attributes
(«ОБОЗНАЧЕНИЕ», «РАЗРАБ»…) or from the texts in the corner of the frame: the
labels of the cells («Разраб.», «Масштаб», «Стадия»…) tell where the values are.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .drawing import MODEL, Box, Drawing, Entity

# ГОСТ 2.301: main formats and the multiples used in practice, (short side, long side) in mm
FORMATS = {"А4": (210, 297), "А3": (297, 420), "А2": (420, 594), "А1": (594, 841), "А0": (841, 1189),
           "А4×3": (297, 630), "А4×4": (297, 841), "А4×5": (297, 1051), "А3×3": (420, 891), "А3×4": (420, 1189),
           "А2×3": (594, 1261)}
# ГОСТ 2.302: scales of reduction (1:n) and of enlargement (n:1); the large ones are for site plans (СПДС)
REDUCTION = (1, 2, 2.5, 4, 5, 10, 15, 20, 25, 40, 50, 75, 100, 200, 400, 500, 800, 1000, 2000, 5000, 10000)
ENLARGEMENT = (2, 2.5, 4, 5, 10, 20, 40, 50, 100)
ESKD, SPDS = "ЕСКД", "СПДС"
STAMP_W, STAMP_H = 185.0, 55.0  # forms 1 (ЕСКД) and 3 (СПДС), mm
LEFT_W = 65.0  # the signatures part of the stamp

FIELD_TITLES = {
    "designation": "Обозначение", "name": "Наименование", "material": "Материал", "scale": "Масштаб",
    "mass": "Масса", "lit": "Литера", "sheet": "Лист", "sheets": "Листов", "stage": "Стадия", "org": "Организация",
    "developer": "Разраб.", "checker": "Пров.", "tcontrol": "Т. контр.", "ncontrol": "Н. контр.",
    "approver": "Утв.", "gip": "ГИП", "developer_date": "Дата (Разраб.)",
}
# what the norm controller returns the drawing for: (field, level) — level 0 «Внимание», 1 «Проверьте»
REQUIRED = {
    ESKD: (("designation", 0), ("name", 0), ("scale", 0), ("developer", 0), ("checker", 1), ("developer_date", 1)),
    SPDS: (("designation", 0), ("name", 0), ("stage", 0), ("sheet", 0), ("developer", 0), ("developer_date", 1)),
}

# attribute tags (upper case, letters and digits only) by their beginning; the order matters
TAGS = (
    ("sheets", ("ЛИСТОВ", "SHEETS", "ВСЕГОЛИСТ", "КОЛЛИСТ")),
    ("designation", ("ОБОЗНАЧ", "ОБОЗН", "ШИФР", "ДЕЦИМ", "DESIGNATION", "DOCNUM", "DWGNO", "CODE")),
    ("name", ("НАИМЕН", "НАЗВАН", "NAME", "TITLE")),
    ("scale", ("МАСШТ", "SCALE")),
    ("mass", ("МАССА", "MASS", "WEIGHT")),
    ("material", ("МАТЕР", "MATERIAL")),
    ("lit", ("ЛИТ",)),
    ("sheet", ("ЛИСТ", "SHEET")),
    ("stage", ("СТАДИЯ", "СТАД", "STAGE")),
    ("tcontrol", ("ТКОНТР",)),
    ("ncontrol", ("НКОНТР", "NKONTR", "NCONTR")),
    ("developer", ("РАЗРАБ", "РАЗР", "DEVELOP", "DESIGNED", "АВТОР")),
    ("checker", ("ПРОВ", "CHECKED", "CHECK")),
    ("approver", ("УТВ", "APPROV")),
    ("gip", ("ГИП", "GIP")),
    ("org", ("ОРГАН", "ФИРМА", "ПРЕДПР", "COMPANY")),
)
# labels of the cells (lower case, letters only) -> field; None: a label of the table, not a field
LABELS = {
    "разраб": "developer", "разработал": "developer", "пров": "checker", "проверил": "checker",
    "тконтр": "tcontrol", "нконтр": "ncontrol", "утв": "approver", "утвердил": "approver", "гип": "gip",
    "лит": "lit", "литера": "lit", "масса": "mass", "масштаб": "scale", "стадия": "stage",
    "лист": "sheet", "листов": "sheets",
    "изм": None, "колуч": None, "№докум": None, "№док": None, "докум": None, "подп": None, "дата": None,
    "копировал": None, "формат": None, "инвподл": None, "подпидата": None, "взаминв": None,
}
ROW_FIELDS = ("developer", "checker", "tcontrol", "ncontrol", "approver", "gip")
SCALE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)")
DATE_RE = re.compile(r"\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?")


@dataclass
class Stamp:
    kind: str  # "block": a block with attributes; "text": texts in the corner of the frame
    norm: str  # ESKD | SPDS
    fields: dict[str, str] = field(default_factory=dict)
    known: set[str] = field(default_factory=set)  # the fields the stamp has: a tag or a label
    block: str = ""

    def value(self, key: str) -> str:
        return (self.fields.get(key) or "").strip()


@dataclass
class Sheet:
    space: str  # MODEL or the name of the layout
    format: str  # "А3", "А4×3"; "" — the size is not a standard format
    landscape: bool
    k: float  # drawing units per paper mm: 2 — the frame is drawn twice its size (scale 1:2 in the model)
    box: Box  # the outer frame
    stamp: Stamp | None = None
    viewport_scales: list[float] = field(default_factory=list)  # paper/model of the views, the largest first

    @property
    def title(self) -> str:
        where = "Модель" if self.space == MODEL else f"Лист «{self.space}»"
        size = f"{self.format}{' альбомный' if self.landscape and self.format else ''}" or "формат не определён"
        designation = self.stamp.value("designation") if self.stamp else ""
        return f"{where}, {size}" + (f", {designation}" if designation else "")

    @property
    def scale(self) -> float | None:
        """paper/model of the drawing: 0.5 for 1:2, 2 for 2:1."""
        if self.space != MODEL:
            return self.viewport_scales[0] if self.viewport_scales else None
        return 1 / self.k if self.k else None

    def stamp_box(self) -> Box:
        x1, y0 = self.box[2] - 5 * self.k, self.box[1] + 5 * self.k
        return (x1 - STAMP_W * self.k, y0, x1, y0 + STAMP_H * self.k)


# ── scales and formats ──────────────────────────────────────────────────────


def nice_k(k: float) -> float | None:
    """The scale factor of a frame (units per paper mm) if it is one of ГОСТ 2.302."""
    for n in REDUCTION:
        if abs(k - n) <= n * 0.005:
            return float(n)
    for n in ENLARGEMENT:
        if abs(k - 1 / n) <= 0.005 / n:
            return 1 / n
    return None


def is_standard_scale(ratio: float) -> bool:
    """*ratio* paper/model: 0.5 for 1:2."""
    return ratio > 0 and nice_k(1 / ratio) is not None


def scale_text(ratio: float | None) -> str:
    if not ratio:
        return ""
    if ratio >= 1:
        return f"{_num(ratio)}:1"
    return f"1:{_num(1 / ratio)}"


def _num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def parse_scale(text: str) -> float | None:
    m = SCALE_RE.search(text or "")
    if not m:
        return None
    a, b = (float(g.replace(",", ".")) for g in m.groups())
    return a / b if a and b else None


def match_format(w: float, h: float) -> tuple[str, bool, float] | None:
    """(format, landscape, k) for a box of *w*×*h* units: the outer frame or the inner one
    (20 mm left, 5 mm on the other sides) of a standard format at a standard scale."""
    if w <= 0 or h <= 0:
        return None
    for name, (short, long) in FORMATS.items():
        for fw, fh, landscape in ((short, long, False), (long, short, True)):
            for inner_w, inner_h in ((fw, fh), (fw - 25, fh - 10)):
                k = nice_k(w / inner_w)
                if k is not None and abs(h - inner_h * k) <= inner_h * k * 0.005:
                    return name, landscape, k
    return None


def _outer(box: Box, w_mm: float, k: float) -> Box:
    """The outer frame for a box matched as the inner one."""
    x0, y0, x1, y1 = box
    if abs((x1 - x0) - w_mm * k) <= w_mm * k * 0.005:
        return box
    return (x0 - 20 * k, y0 - 5 * k, x1 + 5 * k, y1 + 5 * k)


def _same(a: Box, b: Box, tol: float) -> bool:
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def inside(point: tuple[float, float], box: Box, tol: float = 0.0) -> bool:
    return box[0] - tol <= point[0] <= box[2] + tol and box[1] - tol <= point[1] <= box[3] + tol


def contains(outer: Box, inner: Box, tol: float = 0.0) -> bool:
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def _frame(box: Box) -> tuple[str, bool, float, Box] | None:
    w, h = box[2] - box[0], box[3] - box[1]
    got = match_format(w, h)
    if got is None:
        return None
    name, landscape, k = got
    short, long = FORMATS[name]
    return name, landscape, k, _outer(box, long if landscape else short, k)


def find_sheets(drawing: Drawing, space: str) -> list[Sheet]:
    """The frames of a space, the largest first; frames inside a larger one of the same
    size (the inner frame of the outer one) are one sheet."""
    entities = drawing.in_space(space)
    candidates = [e for e in entities if e.box and (e.kind == "INSERT" or (e.kind in ("LWPOLYLINE", "POLYLINE")
                                                                         and e.closed))]
    sheets: list[Sheet] = []
    for e in sorted(candidates, key=lambda c: -(c.box[2] - c.box[0]) * (c.box[3] - c.box[1])):
        got = _frame(e.box)
        if got is None:
            continue
        name, landscape, k, outer = got
        if any(_same(s.box, outer, 2 * k) for s in sheets):
            continue
        sheets.append(Sheet(space, name, landscape, k, outer))
    if not sheets:
        boxes = [e.box for e in entities if e.box and e.kind != "VIEWPORT"
                 and drawing.layers.get(e.layer) is not None and drawing.layers[e.layer].printed]
        if boxes:
            ext = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                   max(b[3] for b in boxes))
            got = _frame(ext)
            if got is not None:
                name, landscape, k, outer = got
                sheets.append(Sheet(space, name, landscape, k, outer))
    if space != MODEL:
        scales = sorted(((e.value, (e.box[2] - e.box[0]) * (e.box[3] - e.box[1])) for e in entities
                         if e.kind == "VIEWPORT" and e.value and e.box), key=lambda t: -t[1])
        for s in sheets:
            s.viewport_scales = [v for v, _ in scales]
    return sheets


# ── title block ─────────────────────────────────────────────────────────────


def _tag_key(tag: str) -> str:
    return re.sub(r"[^0-9A-ZА-ЯЁ]", "", tag.upper()).replace("Ё", "Е")


def field_of_tag(tag: str) -> str | None:
    key = _tag_key(tag)
    date = False
    for word in ("ДАТА", "DATE"):
        if word in key:
            date, key = True, key.replace(word, "")
    for name, starts in TAGS:
        if any(key.startswith(s) for s in starts):
            if date:
                return f"{name}_date" if name in ROW_FIELDS else None
            return name
    return None


def label_of(text: str) -> tuple[bool, str | None]:
    """(is a label, its field) for a text of a stamp cell."""
    key = re.sub(r"[^а-яё№a-z]", "", (text or "").lower()).replace("ё", "е")
    if key in LABELS:
        return True, LABELS[key]
    return False, None


def _norm(known: set[str], texts: list[str]) -> str:
    if known & {"stage", "gip"}:
        return SPDS
    joined = " ".join(texts).lower()
    if "стадия" in joined or "кол.уч" in joined.replace(" ", ""):
        return SPDS
    return ESKD


def _block_stamp(sheet: Sheet, inserts: list[Entity]) -> Stamp | None:
    best, best_fields = None, {}
    for e in inserts:
        if not e.attribs or not inside((e.x, e.y), sheet.box, sheet.k):
            continue
        fields: dict[str, str] = {}
        for tag, value in e.attribs.items():
            key = field_of_tag(tag)
            if key and (key not in fields or not fields[key].strip()):
                fields[key] = value
        core = set(fields) & {"designation", "name", "scale", "developer", "sheet", "stage", "mass"}
        if len(core) >= 2 and len(fields) > len(best_fields):
            best, best_fields = e, fields
    if best is None:
        return None
    known = set(best_fields)
    return Stamp("block", _norm(known, list(best.attribs)), best_fields, known, best.block)


def _text_stamp(sheet: Sheet, texts: list[Entity]) -> Stamp | None:
    k = sheet.k
    zone = sheet.stamp_box()
    cells = [t for t in texts if t.box and t.text.strip() and inside((t.x, t.y), zone, k)]
    if not cells:
        return None
    x0, y0 = zone[0], zone[1]
    labels, values = [], []
    for t in cells:
        is_label, key = label_of(t.text)
        (labels if is_label else values).append((t, key))
    fields: dict[str, str] = {}
    known = {"designation", "name"}

    def rel(t: Entity) -> tuple[float, float]:
        return (t.x - x0) / k, (t.y - y0) / k

    def join(items: list[Entity]) -> str:
        return " ".join(t.text.strip() for t in sorted(items, key=lambda t: (-round(t.y / k), t.x)))

    plain = [t for t, _ in values]
    for t, key in labels:
        if key is None:
            continue
        lx, ly = rel(t)
        if key in ROW_FIELDS and lx < LEFT_W:
            known.update((key, f"{key}_date"))
            row = [v for v in plain if abs(rel(v)[1] - ly) <= 2.5]
            name = [v for v in row if lx < rel(v)[0] <= 40 and not DATE_RE.fullmatch(v.text.strip())]
            date = [v for v in row if 52 <= rel(v)[0] <= LEFT_W or DATE_RE.fullmatch(v.text.strip())]
            fields[key], fields[f"{key}_date"] = join(name), join(date)
        elif key in ("scale", "mass", "lit", "stage", "sheet", "sheets") and lx >= LEFT_W:
            known.add(key)
            below = [v for v in plain if 1 <= ly - rel(v)[1] <= 16 and abs(rel(v)[0] - lx) <= 9]
            right = [v for v in plain if abs(rel(v)[1] - ly) <= 2.5 and 0 < rel(v)[0] - lx <= 22]
            if key in ("sheet", "sheets"):
                below = [v for v in below if ly - rel(v)[1] <= 10]
                chosen = below or right
            else:
                chosen = below
            if key == "scale":
                chosen = [v for v in chosen if parse_scale(v.text)] or chosen
            fields[key] = join(chosen[:1] if key in ("sheet", "sheets") else chosen)
    taken = {fields[f] for f in fields if fields[f]}
    right_part = [v for v in plain if rel(v)[0] >= LEFT_W]
    fields["designation"] = join([v for v in right_part if rel(v)[1] >= 40])
    norm = _norm(set(f for f in known if f in fields and f not in ("designation", "name")),
                 [t.text for t, _ in labels])
    name_cell = [v for v in right_part if rel(v)[0] < 135 and (15 if norm == ESKD else 0) <= rel(v)[1] < 40
                 and v.text.strip() not in taken]
    fields["name"] = join(name_cell)
    if norm == ESKD:
        known.add("material")
        fields["material"] = join([v for v in right_part if rel(v)[0] < 135 and rel(v)[1] < 15])
    known.add("org")
    fields["org"] = join([v for v in right_part if rel(v)[0] >= 135 and rel(v)[1] < 15 and v.text.strip() not in taken])
    if not any(k for k, _ in labels) and not fields["designation"] and not fields["name"]:
        return None
    return Stamp("text", norm, fields, known)


def read_stamp(drawing: Drawing, sheet: Sheet, norm: str | None = None) -> Stamp | None:
    entities = drawing.in_space(sheet.space)
    stamp = _block_stamp(sheet, [e for e in entities if e.kind == "INSERT"])
    if stamp is None:
        texts = [e for e in entities if e.kind in ("TEXT", "MTEXT", "BLOCKTEXT")]
        stamp = _text_stamp(sheet, texts)
    if stamp is not None and norm:
        stamp.norm = norm
    return stamp


def sheets(drawing: Drawing, norm: str | None = None) -> list[Sheet]:
    """Every sheet of the drawing with its title block (None when there is none)."""
    out = []
    for space in drawing.spaces:
        for sheet in find_sheets(drawing, space):
            sheet.stamp = read_stamp(drawing, sheet, norm)
            out.append(sheet)
    return out
