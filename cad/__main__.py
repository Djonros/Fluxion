"""CLI: python -m cad "фланец 150, внутри 50, 6 отверстий 11 на 110, толщина 15"

  python -m cad "описание"        — модель разбирает описание, деталь строится в КОМПАСе
  python -m cad --demo            — все шаблоны на примерах, без модели
  python -m cad --spec file.json  — готовый JSON (шаблон+параметры или список операций, "material")
  --dry-run                       — только показать JSON, КОМПАС не трогать
  --no-ask                        — не спрашивать недостающие размеры и материал, взять по умолчанию
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cad", description="Деталь в КОМПАС-3D по описанию")
    ap.add_argument("description", nargs="*", help="описание детали")
    ap.add_argument("--demo", action="store_true", help="построить все шаблоны")
    ap.add_argument("--spec", help="JSON-файл")
    ap.add_argument("--dry-run", action="store_true", help="только показать JSON")
    ap.add_argument("--out", help="папка для .m3d (по умолчанию Документы\\Fluxion CAD)")
    ap.add_argument("--no-ask", action="store_true", help="не задавать вопросов: недостающее — по умолчанию")
    ap.add_argument("--only", help="с --demo: только эти шаблоны через запятую (например sheet_angle,sheet_z)")
    args = ap.parse_args(argv)

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
