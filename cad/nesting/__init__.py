"""«Раскрой листа»: details of an Excel order nested on the remnants of the shop and on whole
sheets, a DXF and a picture of every sheet, the report and the changes of the remnants.

- orders.py — the order as the plant keeps it («Лист 8 (09Г2С)», «Кол-во на 1 шт.»);
- stock.py — the list of remnants («8 09Г2С», «1250х880», sketches, «Задействованные»);
- parts.py — the DXF of a designation in the folders or the «Архив КД», its contour and holes;
- engine.py — the nesting of the true shape;
- cutting.py — the cutting path: kerf, lead-ins, overcuts, bridges, the order of the cuts;
- post.py — the program of a machine by its JSON profile (machines/*.json);
- output.py — DXF, PNG, programs, «Отчёт раскроя.xlsx», «Остатки — изменения.xlsx».

``run`` does it all; ``python -m cad.nesting заявка.xlsx --остатки остатки.xlsx`` from a console.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .engine import Result, Settings, nest
from .orders import Order, read_order
from .parts import DxfIndex, Part, archive_lookup, resolve
from .stock import Stock, read_stock


@dataclass
class Job:
    order: Order
    stock: Stock | None
    parts: list[Part]
    result: Result | None = None
    paths: dict | None = None
    text: str = ""


def prepare(order_path: str | Path, stock_path: str | Path | None = None, dxf_folders=(), products: int | None = None,
            archive_dir: str | Path | None = None, progress=lambda text: None) -> Job:
    """The order read, the remnants read, the DXF of every detail found and read."""
    order = read_order(order_path, products)
    stock = read_stock(stock_path) if stock_path else None
    folders = [Path(order_path).parent, *[Path(f) for f in dxf_folders if f]]
    progress("Ищу DXF деталей…")
    index = DxfIndex.build(folders)
    lookup = archive_lookup(archive_dir) if archive_dir else None
    parts = resolve(order.items, folders, base=Path(order_path).parent, index=index, archive_lookup=lookup)
    return Job(order, stock, parts)


def run(job: Job, settings: Settings | None = None, out: str | Path | None = None,
        progress=lambda text: None, machine=None, cut=None) -> Job:
    """The nesting of a prepared job and its files; with *machine* (post.Machine) and *cut*
    (cutting.CutSettings) — the programs of the sheets too."""
    from . import output

    settings = settings or Settings()
    if not settings.order:
        settings.order = job.order.number
    job.result = nest(job.parts, job.stock.sheets if job.stock else [], settings, progress)
    progress("Сохраняю DXF, картинки и отчёт…")
    folder = Path(out) if out else output.default_folder(job.order)
    job.paths = output.save_all(job.order, job.parts, job.result, folder, job.stock.path if job.stock else None,
                                machine, cut)
    job.text = output.summary_text(job.order, job.parts, job.result, job.paths.get("minutes", 0.0))
    return job


__all__ = ["Job", "Settings", "prepare", "run"]
