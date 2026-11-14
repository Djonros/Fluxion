"""python -m cad.nesting заявка.xlsx [--остатки остатки.xlsx] [--dxf папка] [--изделий N] [--out папка]"""
from __future__ import annotations

import argparse
import sys

from . import Settings, prepare, run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cad.nesting", description="Раскрой листа по заявке Excel")
    ap.add_argument("order", help="заявка Excel: Обозначение, Наименование, Кол-во, Материал")
    ap.add_argument("--остатки", "--stock", dest="stock", help="список деловых остатков Excel")
    ap.add_argument("--dxf", action="append", default=[], help="папка с DXF (можно несколько)")
    ap.add_argument("--изделий", "--products", dest="products", type=int, help="сколько изделий")
    ap.add_argument("--out", help="папка результата (по умолчанию Документы\\Fluxion CAD\\Раскрой)")
    ap.add_argument("--зазор", "--gap", dest="gap", type=float, default=8.0, help="зазор между деталями, мм")
    ap.add_argument("--отступ", "--margin", dest="margin", type=float, default=10.0, help="отступ от края, мм")
    ap.add_argument("--лист", "--sheet", dest="sheet", default="6000x1500", help="стандартный лист, мм")
    ap.add_argument("--станок", "--machine", dest="machine", default="",
                    help="профиль станка для программ (hasa-aria-maxcut250, iso-gcode); без него — без программ")
    ap.add_argument("--мостики", "--bridges", dest="bridges", type=int, default=0, help="мостиков на контур")
    args = ap.parse_args(argv)
    from .stock import parse_size

    size = parse_size(args.sheet) or (6000.0, 1500.0)
    job = prepare(args.order, args.stock, args.dxf, args.products, progress=print)
    for problem in job.order.problems + (job.stock.problems if job.stock else []):
        print("!", problem)
    machine = None
    if args.machine:
        from .post import find_machine

        machine = find_machine(args.machine)
        if machine is None:
            print("! Нет профиля станка:", args.machine)
            return 2
    from .cutting import CutSettings

    run(job, Settings(gap=args.gap, margin=args.margin, sheet_sizes=(size,)), args.out, progress=print,
        machine=machine, cut=CutSettings(bridges=args.bridges))
    print(job.text)
    print("Папка:", job.paths["folder"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
