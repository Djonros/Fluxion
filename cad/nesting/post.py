"""The post-processor: the cuts of a sheet (cutting.py) as a program for a machine.

A machine is a profile — a JSON file, as a ProNest machine is a set of settings:
``cad/nesting/machines/*.json`` come with Fluxion, ``%APPDATA%\\Fluxion\\machines\\*.json``
are the user's (the same ``id`` replaces the shipped one). The keys:

- ``name``, ``id``, ``extension`` (".CNC"), ``encoding`` ("ascii"), ``newline`` ("\\r\\n");
- ``non_ascii``: "?" (as ProNest writes Cyrillic for an ASCII machine) or "translit";
- ``decimals`` (3), ``separator`` between words ("" — «G01X5.Y2.»);
- ``program_start``, ``program_end``: lines; ``cut_start``: lines before a cut —
  ``{x}``, ``{y}`` (the start), ``{seq}``, ``{name}``, ``{feed}``; ``cut_end``: after it;
- ``thc_off`` with ``thc_off_before_end`` mm: the line that holds the torch height near the
  end of a cut (M62P36 on HASA); "" — none;
- ``line``, ``arc_cw``, ``arc_ccw``: the G words; ``modal_motion``: a G word only when it
  changes; ``modal_axes``: an axis only when it changes;
- ``arc_center``: "incremental" (I, J from the start of the arc) or "absolute";
- ``line_numbers`` (false), ``line_number_step`` (10);
- ``technology``: rows ``[thickness, kerf, feed, pierce s, lead-in, overcut, height hold]``
  (mm, mm/min): the last three may be left out — then the window's values and
  ``thc_off_before_end`` are used. Between two rows the values go linearly.

Numbers: «788.189», «0.», «3.» — no trailing zeros, the dot kept.
"""
from __future__ import annotations

import itertools
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from .cutting import Cut, Move, SheetPath, fit_arcs

BUILTIN_DIR = Path(__file__).with_name("machines")
_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


@dataclass
class Machine:
    id: str
    name: str
    extension: str = ".nc"
    encoding: str = "ascii"
    newline: str = "\r\n"
    non_ascii: str = "?"
    decimals: int = 3
    separator: str = ""
    program_start: list[str] = field(default_factory=list)
    program_end: list[str] = field(default_factory=lambda: ["M30"])
    cut_start: list[str] = field(default_factory=lambda: ["G00X{x}Y{y}", "F{feed}", "M03"])
    cut_end: list[str] = field(default_factory=lambda: ["M05"])
    thc_off: str = ""
    thc_off_before_end: float = 0.0
    line: str = "G01"
    arc_cw: str = "G02"
    arc_ccw: str = "G03"
    modal_motion: bool = True
    modal_axes: bool = True
    arc_center: str = "incremental"
    arcs: bool = True
    line_numbers: bool = False
    line_number_step: int = 10
    process: str = "plasma"
    technology: list = field(default_factory=list)  # [thickness, kerf, feed, pierce, lead-in, overcut, hold]
    technology_note: str = ""
    source: str = "builtin"

    def tech(self, thickness: float) -> tuple[float, float, float]:
        """(kerf, feed, pierce) for *thickness*: linear between the rows, the end rows beyond."""
        t = self.technology_for(thickness)
        return t["kerf"], t["feed"], t["pierce"]

    def technology_for(self, thickness: float) -> dict:
        """kerf, feed, pierce, lead_in, overcut, hold for *thickness* (0 — not in the table):
        linear between the rows that have the value, the end rows beyond."""
        names = ("kerf", "feed", "pierce", "lead_in", "overcut", "hold")
        default = {"kerf": 1.7, "feed": 3000.0, "pierce": 0.5}
        out = {}
        for k, name in enumerate(names, start=1):
            rows = sorted((float(r[0]), float(r[k])) for r in self.technology
                          if len(r) > k and r[k] is not None and len(r) >= 3)
            out[name] = round(_between(rows, thickness), 3) if rows else default.get(name, 0.0)
        return out

    @classmethod
    def from_json(cls, data: dict, source: str = "builtin") -> Machine:
        known = {f for f in cls.__dataclass_fields__}
        values = {k: v for k, v in data.items() if k in known}
        if "id" not in values or "name" not in values:
            raise ValueError("в профиле станка нужны «id» и «name»")
        values["source"] = source
        return cls(**values)


def _between(rows: list[tuple[float, float]], x: float) -> float:
    if x <= rows[0][0]:
        return rows[0][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(rows):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * ((x - x0) / (x1 - x0) if x1 > x0 else 0)
    return rows[-1][1]


def user_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Fluxion" / "machines"


def load_machines(extra: Path | None = None) -> tuple[list[Machine], list[str]]:
    """The shipped profiles and the user's ones (the same id replaces); the errors of bad files."""
    machines: dict[str, Machine] = {}
    errors = []
    for folder, source in ((BUILTIN_DIR, "builtin"), (extra or user_dir(), "user")):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                machine = Machine.from_json(json.loads(path.read_text(encoding="utf-8-sig")), str(path))
            except (OSError, ValueError, TypeError) as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            if source == "builtin":
                machine.source = "builtin"
            machines[machine.id] = machine
    return list(machines.values()), errors


def find_machine(machine_id: str) -> Machine | None:
    return next((m for m in load_machines()[0] if m.id == machine_id), None)


def copy_to_user(machine: Machine) -> Path:
    """The profile as the user's file, to be edited (Notepad): the copy wins over the shipped one."""
    folder = user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{machine.id}.json"
    if not path.exists():
        data = {k: getattr(machine, k) for k in machine.__dataclass_fields__ if k != "source"}
        data["id"] = machine.id
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── the program ──────────────────────────────────────────────────────────────


def number(value: float, decimals: int = 3) -> str:
    """788.189, 0., 3., -12.5 — as ProNest writes them."""
    text = f"{round(value, decimals) + 0.0:.{decimals}f}".rstrip("0")
    if text in ("-0.", "-."):
        text = "0."
    return text


def _text(text: str, machine: Machine) -> str:
    out = []
    for ch in text:
        if ord(ch) < 128:
            out.append(ch)
        elif machine.non_ascii == "translit":
            low = ch.lower()
            t = _TRANSLIT.get(low, "?")
            out.append(t.upper() if ch != low and t else t)
        else:
            out.append(machine.non_ascii)
    return "".join(out).replace("(", "[").replace(")", "]") if machine.non_ascii else "".join(out)


def _split(start, move: Move, at: float) -> tuple[Move, Move]:
    """*move* cut at *at* mm from *start*."""
    if move.kind == "line":
        length = math.dist(start, move.end)
        t = at / length if length else 0
        point = (start[0] + (move.end[0] - start[0]) * t, start[1] + (move.end[1] - start[1]) * t)
        return Move("line", point), move
    cx, cy = move.center
    r = math.dist(move.center, start)
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    turn = at / r * (-1 if move.cw else 1)
    point = (cx + r * math.cos(a0 + turn), cy + r * math.sin(a0 + turn))
    return Move("arc", point, move.center, move.cw), move


def _thc_split(cut: Cut, before_end: float) -> tuple[list[Move], int]:
    """The moves with a point *before_end* mm before the end; the index of the move after
    which the height is held (-1 — from the start)."""
    from .cutting import _move_length

    moves = list(cut.moves)
    total = cut.length
    if before_end <= 0:
        return moves, len(moves)
    if before_end >= total:
        return moves, -1
    target = total - before_end
    run, p = 0.0, cut.start
    for k, m in enumerate(moves):
        length = _move_length(p, m)
        if run + length >= target - 1e-9:
            at = target - run
            if at <= 1e-6:
                return moves, k - 1
            if at >= length - 1e-6:
                return moves, k
            first, rest = _split(p, m, at)
            return moves[:k] + [first, rest] + moves[k + 1:], k
        run += length
        p = m.end
    return moves, len(moves) - 1


class Program:
    def __init__(self, machine: Machine):
        self.machine = machine
        self.lines: list[str] = []
        self.mode = ""
        self.x = self.y = None  # the raw position: an axis is written when it moves at all

    def emit(self, text: str) -> None:
        if text:
            self.lines.append(text)

    def words(self, *words: str) -> None:
        self.emit(self.machine.separator.join(w for w in words if w))

    def fmt(self, value: float) -> str:
        return number(value, self.machine.decimals)

    def template(self, line: str, **values) -> None:
        self.emit(line.format(**values))

    def move(self, m: Move, start) -> None:
        mc = self.machine
        if m.kind == "line":
            g = mc.line
        else:
            g = mc.arc_cw if m.cw else mc.arc_ccw
        words = [g if (not mc.modal_motion or g != self.mode) else ""]
        x, y = self.fmt(m.end[0]), self.fmt(m.end[1])
        always = not mc.modal_axes or m.kind == "arc"
        moved_x = self.x is None or abs(m.end[0] - self.x) > 1e-6
        moved_y = self.y is None or abs(m.end[1] - self.y) > 1e-6
        words.append("X" + x if always or moved_x else "")  # as ProNest: «X5108.03Y1034.077»
        words.append("Y" + y if always or moved_y else "")  # when the edge is a hair off square
        if m.kind == "arc":
            if mc.arc_center == "absolute":
                i, j = m.center[0], m.center[1]
            else:
                i, j = m.center[0] - start[0], m.center[1] - start[1]
            words += ["I" + self.fmt(i), "J" + self.fmt(j)]
        if not any(w for w in words[1:]):
            return  # a move to where the torch is
        self.words(*words)
        self.mode, self.x, self.y = g, m.end[0], m.end[1]


def program(path: SheetPath, machine: Machine, feed: float, title: str = "") -> str:
    """The text of the program of one sheet."""
    p = Program(machine)
    for line in machine.program_start:
        p.template(line, title=_text(title, machine))
    for seq, cut in enumerate(path.cuts, start=1):
        sx, sy = p.fmt(cut.start[0]), p.fmt(cut.start[1])
        for line in machine.cut_start:
            p.template(line, x=sx, y=sy, seq=seq, name=_text(cut.name, machine), feed=f"{feed:.0f}")
        p.mode, p.x, p.y = "G00", cut.start[0], cut.start[1]
        moves, hold = _thc_split(cut, machine.thc_off_before_end if machine.thc_off else 0)
        if machine.arcs:
            pieces = []  # arcs are fitted apart before and after the hold point
            for chunk_start, chunk in ((cut.start, moves[:hold + 1]),
                                       (moves[hold].end if 0 <= hold < len(moves) else cut.start, moves[hold + 1:])):
                pieces.append(fit_arcs(chunk_start, chunk) if chunk else [])
            moves, hold = pieces[0] + pieces[1], len(pieces[0]) - 1
        if hold < 0 and machine.thc_off:
            p.emit(machine.thc_off)
        position = cut.start
        for k, m in enumerate(moves):
            p.move(m, position)
            position = m.end
            if k == hold and machine.thc_off and k < len(moves) - 1:
                p.emit(machine.thc_off)
        for line in machine.cut_end:
            p.template(line, seq=seq)
    for line in machine.program_end:
        p.template(line)
    lines = p.lines
    if machine.line_numbers:
        lines = [f"N{(k + 1) * machine.line_number_step}{machine.separator}{text}" for k, text in enumerate(lines)]
    return machine.newline.join(lines) + machine.newline


def save(text: str, path: Path, machine: Machine) -> Path:
    path.write_bytes(text.encode(machine.encoding, errors="replace"))
    return path
