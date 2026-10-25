"""CLI: python -m cad "фланец 150, внутри 50, 6 отверстий 11 на 110, толщина 15"

  python -m cad "описание"        — модель разбирает описание, деталь строится в КОМПАСе
  python -m cad --demo            — все шаблоны на примерах, без модели
  python -m cad --spec file.json  — готовый JSON (шаблон+параметры или список операций, "material")
  --dry-run                       — только показать JSON, КОМПАС не трогать
  --no-ask                        — не спрашивать недостающие размеры и материал, взять по умолчанию
  python -m cad --report file.a3d — сводная ведомость, детали, металлоёмкость, сварка и замечания в Excel
                                    (сборка открывается только для чтения; --out — папка отчёта)
  python -m cad --acad file.dwg   — нормоконтроль чертежа AutoCAD (DWG/DXF) по ЕСКД или СПДС, отчёт в Excel
                                    (--norm ЕСКД|СПДС, --check, --accept, --out как у --report)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import materials, spec

# materials of the demo parts (the rest are Сталь 45 / лист Ст3)
DEMO_MATERIALS = {"plate": "Д16Т", "bushing": "бронза", "nut": "12Х18Н10Т", "sheet_bracket": "09Г2С",
                  "sheet_channel": "оцинковка", "sheet_z": "08пс", "sheet_tray": "оцинковка", "sheet_shelf": "АМг2"}


def _load_backend():
    from core.backend_factory import BackendFactory
    from core.config import Settings

    return BackendFactory.create(Settings.load())


def _run(part: dict, args) -> bool:
    print(json.dumps(part, ensure_ascii=False, indent=2) if args.dry_run else
          f"Строю «{part['name']}» ({len(part['ops'])} операций)…")
    if args.dry_run:
        return True
    from . import kompas

    try:
        result = kompas.build_part(part, kompas.part_path(part["name"], args.out))
    except kompas.KompasError as exc:
        print(f"[ОШИБКА] {exc}")
        return False
    except Exception as exc:  # COM errors carry the KOMPAS message
        print(f"[ОШИБКА КОМПАС] {type(exc).__name__}: {exc}")
        return False
    from .text2cad import result_text

    print(result_text(part, result))
    return True


def _ask_until_built(description: str, backend, ask: bool) -> dict | None:
    """Run the conversation in the console: questions via input()."""
    from . import text2cad

    conversation = text2cad.Conversation(backend, ask=ask)
    message = description
    while True:
        print("Разбираю описание моделью…")
        reply = conversation.step(message)
        print(reply.text)
        if reply.kind != "ask":
            return reply.part
        try:
            message = input("> ").strip()
        except EOFError:  # no console: build with the defaults
            message = "по умолчанию"


def _pro(feature: str, text: str) -> bool:
    """Is *feature* of Fluxion Pro on? If not, say so on the console."""
    from licensing import feature_enabled

    if feature_enabled(feature):
        return True
    print(text + " Активировать: в программе меню «Помощь» → «Лицензия…».")
    return False


def _report(path: str, out: str | None, check_only: bool = False, accept: bool = False) -> int:
    from . import assembly, report

    if not _pro(report.PRO_FEATURE, "«Ведомость и металлоёмкость сборки» — функция Pro: сводная ведомость, металлоёмкость, сварка, покраска и трудоёмкость сборки КОМПАС-3D в Excel, нормоконтроль перед отчётом, «Исправить в модели» и «Задать материал»."):
        return 2

    try:
        top = assembly.read(path)
    except assembly.AssemblyError as exc:
        print(f"[ОШИБКА] {exc}")
        return 1
    result = report.build(top, report.load_decisions(top.file))
    report.add_welds(result)
    print(report.warnings_text(result, limit=30))
    print()
    if check_only:
        return 0
    if result.unchecked and not accept:
        print(f"[ОТЧЁТ НЕ СФОРМИРОВАН] Не отмечено предупреждений: {len(result.unchecked)}. Отметьте их в Fluxion "
              "(Инструменты → «Ведомость и металлоёмкость сборки…») или запустите с --accept.")
        return 3
    target = report.save_xlsx(result, report.default_path(top, out), source=Path(path).name)
    print(report.summary_text(result, target))
    return 0


def _acad(path: str, out: str | None, norm: str | None, check_only: bool = False, accept: bool = False) -> int:
    from . import acad, acad_check, report

    if not _pro(acad_check.PRO_FEATURE, "«Нормоконтроль чертежа AutoCAD» — функция Pro: штамп, масштабы, шрифты, слои и размеры чертежа DWG/DXF по ЕСКД и СПДС, замечания в Excel."):
        return 2
    from .drawing import DrawingError

    try:
        drawing = acad.open_drawing(path)
    except DrawingError as exc:
        print(f"[ОШИБКА] {exc}")
        return 1
    result = acad_check.check(drawing, report.load_decisions(drawing.file), norm=norm)
    print(acad_check.warnings_text(result, limit=30))
    print()
    if check_only:
        return 0
    if result.unchecked and not accept:
        print(f"[ОТЧЁТ НЕ СФОРМИРОВАН] Не отмечено замечаний: {len(result.unchecked)}. Отметьте их в Fluxion "
              "(Инструменты → «Нормоконтроль чертежа AutoCAD…») или запустите с --accept.")
        return 3
    target = acad_check.save_xlsx(result, acad_check.default_path(result, out))
    print(acad_check.summary_text(result, target))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cad", description="Деталь в КОМПАС-3D по описанию")
    ap.add_argument("description", nargs="*", help="описание детали")
    ap.add_argument("--demo", action="store_true", help="построить все шаблоны")
    ap.add_argument("--spec", help="JSON-файл")
    ap.add_argument("--dry-run", action="store_true", help="только показать JSON")
    ap.add_argument("--out", help="папка для .m3d (по умолчанию Документы\\Fluxion CAD)")
    ap.add_argument("--no-ask", action="store_true", help="не задавать вопросов: недостающее — по умолчанию")
    ap.add_argument("--only", help="с --demo: только эти шаблоны через запятую (например sheet_angle,sheet_z)")
    ap.add_argument("--report", help="сборка .a3d: ведомость и металлоёмкость в Excel")
    ap.add_argument("--check", action="store_true", help="с --report/--acad: только предупреждения, без отчёта")
    ap.add_argument("--accept", action="store_true",
                    help="с --report: сформировать, хотя предупреждения не отмечены (пометка «не проверено»)")
    ap.add_argument("--acad", help="чертёж AutoCAD .dwg/.dxf: нормоконтроль, замечания в Excel")
    ap.add_argument("--norm", choices=("ЕСКД", "СПДС"), help="с --acad: нормы (по умолчанию — по штампу)")
    args = ap.parse_args(argv)
    if args.acad:
        return _acad(args.acad, args.out, args.norm, check_only=args.check, accept=args.accept)
    if args.report:
        return _report(args.report, args.out, check_only=args.check, accept=args.accept)

    parts: list[dict] = []
    try:
        if args.demo:
            only = set(args.only.split(",")) if args.only else None
            for key, tpl in spec.TEMPLATES.items():
                if only and key not in only:
                    continue
                kinds = materials.SHEET_KINDS if tpl.sheet else ("solid",)
                material = (spec.find_material(DEMO_MATERIALS.get(key, ""), kinds)
                            or materials.default("sheet" if tpl.sheet else "solid"))
                parts.append(spec.build(key, tpl.example[1], material=material)[0])
        elif args.spec:
            data = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
            material = spec.find_material(str(data.get("material") or ""))
            if data.get("material") and material is None:
                raise spec.SpecError(f"Незнакомый материал: {data['material']!r}.")
            if "ops" in data:
                spec.validate_ops(data["ops"])
                part = {"name": data.get("name") or "Деталь", "ops": data["ops"]}
                if material is not None:
                    part["material"] = spec.material_record(material)
                parts.append(part)
            else:
                parts.append(spec.build(data["template"], data.get("params"), data.get("name", ""), material)[0])
        elif args.description:
            part = _ask_until_built(" ".join(args.description), _load_backend(), ask=not args.no_ask)
            if part is None:
                return 1
            parts.append(part)
        else:
            ap.print_help()
            return 2
    except spec.SpecError as exc:
        print(f"[ОШИБКА] {exc}")
        return 1

    ok = all([_run(p, args) for p in parts])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
