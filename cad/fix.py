"""Fixes of an assembly model the report warns about, made only with the designer's consent.

A detail without a material gets the one the designer chooses in the «Материалы»
library of KOMPAS (Полином): its designation as KOMPAS keeps it and its density.
The detail file changes (for a local detail — the file it lives in).

A bought unit left out of «Прочие изделия» is moved there in the specification of the
assembly it is in: its specification object gets the section «Прочие изделия», or,
when the component has none (a lamp inserted without one), an object is created
in that section with the designation and the name of the component; an assembly
without a specification gets one (the usual style of GRAPHIC.LYT). The files of
the components (often a shared library of bought units) are never changed.

The order: the list of fixes → the designer's consent → a backup copy of every file
that changes → the fix. A document the designer has open is not changed: they close
it first (its unsaved changes are theirs).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .assembly import (ASSEMBLY, DETAIL, OTHER_SECTION, SPC_MARKING, SPC_NAME, AssemblyError, Component, _get,
                       no_material)

OTHER_SECTION_NUMBER = 30  # «Прочие изделия» in the style of GRAPHIC.LYT
SPC_LAYOUT, SPC_STYLE = "graphic.lyt", 1  # the usual specification of KOMPAS (ГОСТ 2.106)


@dataclass
class SectionFix:
    """Move *component* to «Прочие изделия» in the specification of the assembly at *path*
    (the chain of subassemblies from the product; empty — the product itself)."""

    component: Component
    path: list[Component] = field(default_factory=list)

    @property
    def assembly_title(self) -> str:
        return self.path[-1].title if self.path else ""

    @property
    def file(self) -> str:
        """The file that changes: the nearest subassembly with a file of its own
        (a local one lives in the file of its parent)."""
        for c in reversed(self.path):
            if c.file and ">" not in c.file:
                return c.file
        return ""

    @property
    def text(self) -> str:
        where = f"в «{self.assembly_title}»" if self.path else "в изделии"
        return f"{self.component.title} ({where}) → раздел «{OTHER_SECTION}»"


def section_fixes(top: Component) -> list[SectionFix]:
    """The bought units of the report (guessed by name, kept as bought) that are not
    in «Прочие изделия» of their assembly; each assembly and component once."""
    out: list[SectionFix] = []
    seen: set[tuple] = set()

    def walk(assembly: Component, path: list[Component]) -> None:
        for child in assembly.children:
            if child.guessed and child.section != OTHER_SECTION:
                key = (tuple(c.title for c in path), child.marking, child.name)
                if key not in seen:
                    seen.add(key)
                    out.append(SectionFix(child, list(path)))
            if child.kind == ASSEMBLY:
                walk(child, path + [child])

    walk(top, [])
    return out


def changed_files(top: Component, fixes: list[SectionFix]) -> list[str]:
    """The files the fixes change: the product and the subassemblies with files of their own."""
    files = dict.fromkeys([f.file or top.file for f in fixes])
    return [f for f in files if f]


def backup(path: str | Path, stamp: str | None = None) -> Path:
    """A copy of the file next to it, in «Резервные копии Fluxion», with the date and time."""
    path = Path(path)
    stamp = stamp or datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    folder = path.parent / "Резервные копии Fluxion"
    folder.mkdir(exist_ok=True)
    target = folder / f"{path.stem} {stamp}{path.suffix}"
    shutil.copy2(path, target)
    return target


def _same(part, component: Component) -> bool:
    return (str(_get(part, "Marking", "") or "").strip() == component.marking
            and str(_get(part, "Name", "") or "").strip() == component.name)


def _section_in(doc, component: Component, parts, cast) -> str:
    """Put *component* into «Прочие изделия» of the specification of *doc*; what was done."""
    raw = cast(doc, "IKompasDocument3D").SpecificationDescriptions
    if raw is None:
        return "у сборки нет спецификации — не исправлено"
    descs = cast(raw, "ISpecificationDescriptions")
    created = ""
    if int(descs.Count) == 0:  # the designer agreed: the assembly gets a specification of the usual style
        desc = descs.Add(SPC_LAYOUT, SPC_STYLE, "")
        if desc is None:
            return "у сборки нет спецификации, КОМПАС-3D не создал её — не исправлено"
        cast(desc, "ISpecificationDescription").Update()
        created = "создана спецификация сборки, "
    desc = cast(descs.Item(0), "ISpecificationDescription")
    moved = 0
    for obj in desc.BaseObjects or []:
        obj = cast(obj, "ISpecificationBaseObject")
        columns = cast(obj.Columns, "ISpecificationColumns")
        texts = []
        for column_type in (SPC_MARKING, SPC_NAME):
            column = columns.Column(column_type, 1, 0)
            texts.append(str(cast(column, "ISpecificationColumn").Text.Str or "").strip()
                         if column is not None else "")
        if tuple(texts) == (component.marking, component.name):
            if int(obj.Section) != OTHER_SECTION_NUMBER:
                obj.SetSection(OTHER_SECTION_NUMBER)
                obj.Update()
            moved += 1
    if moved:
        return created + "раздел изменён"
    instances = [p for p in parts if _same(p, component)]
    if not instances:
        return "компонент не найден в сборке — не исправлено"
    objects = cast(desc.BaseObjects, "ISpecificationBaseObjects")
    obj = cast(objects.Add(OTHER_SECTION_NUMBER, 0), "ISpecificationBaseObject")
    for part in instances:  # every instance is one line of the specification, the count is theirs
        obj.IncludeGeometry(part, True)
    obj.Update()
    return created + "создан объект спецификации"


def apply_section_fixes(product_file: str | Path, fixes: list[SectionFix], log=print) -> list[str]:
    """Make the fixes in the model; one result line per fix. The caller made the backups."""
    return _apply(product_file, fixes, log, _fix_section)


def apply_material_fixes(product_file: str | Path, fixes: list["MaterialFix"], material: str, density: float,
                         log=print) -> list[str]:
    """Set *material* (as KOMPAS keeps it) and *density* (g/cm³) to the details; one line per fix."""
    for f in fixes:
        f.material, f.density = material, density
    return _apply(product_file, fixes, log, _fix_material)


def _apply(product_file, fixes, log, fix_one) -> list[str]:
    try:
        from win32com.client import CastTo, Dispatch
    except ImportError as exc:
        raise AssemblyError("Нужен пакет pywin32: pip install pywin32") from exc
    product_file = Path(product_file)
    app = Dispatch("Kompas.Application.7")
    for doc in _get(app, "Documents", None) or []:
        if str(_get(doc, "PathName", "")).lower() == str(product_file).lower():
            raise AssemblyError(f"«{product_file.name}» открыт в КОМПАС-3D: сохраните и закройте его, "
                                "потом повторите исправление.")
    old_hide = _get(app, "HideMessage", None)
    app.HideMessage = 1
    results = []
    doc = app.Documents.Open(str(product_file), False, False)  # invisible, writable
    if doc is None:
        raise AssemblyError(f"КОМПАС-3D не открыл {product_file.name}.")
    try:
        top = CastTo(CastTo(doc, "IKompasDocument3D").TopPart, "IPart7")
        for fix in fixes:
            log(f"Исправляю: {fix.text}")
            try:
                results.append(f"{fix.text}: {fix_one(app, doc, top, fix, CastTo)}")
            except Exception as exc:  # one fix must not stop the others
                results.append(f"{fix.text}: ошибка КОМПАС-3D — {exc}")
        doc.Save()
    finally:
        doc.Close(0)  # saved above; nothing more to keep
        if old_hide is not None:
            try:
                app.HideMessage = old_hide
            except Exception:
                pass
    return results


def _children(part, cast):
    return [cast(c, "IPart7") for c in (_get(part, "Parts", None) or [])]


def _along(top, path: list[Component], cast) -> list:
    """The subassemblies at the end of *path* (identical local copies — each of them)."""
    level = [top]
    for step in path:
        level = [p for parent in level for p in _children(parent, cast) if _same(p, step)]
    return level


def _each_source(app, parts, cast, change) -> list[str]:
    """Open the source document of every part (one per file; a local part — each), writable
    and invisible, *change* it, save and close it. A document the designer has open is left alone."""
    done = []
    files_done: set[str] = set()
    for part in parts:
        file = str(_get(part, "FileName", "") or "").lower()
        if file and ">" not in file:
            if file in files_done:
                continue  # one file is changed once
            files_done.add(file)
        param = part.GetOpenDocumentParam()
        param.Visible, param.ReadOnly = False, False
        before = int(app.Documents.Count)
        source = part.OpenSourceDocument(param)
        if source is None:
            done.append("файл не открылся — не исправлено")
            continue
        if int(app.Documents.Count) <= before:  # it was open already: the designer's, maybe unsaved
            done.append(f"«{Path(file).name}» открыт в КОМПАС-3D — не изменён, закройте его и повторите")
            continue
        try:
            done.append(change(source))
            source.Save()
        finally:
            source.Close(0)
    return done


def _fix_section(app, doc, top, fix: SectionFix, cast) -> str:
    if not fix.path:
        return _section_in(doc, fix.component, _children(top, cast), cast)
    level = _along(top, fix.path, cast)
    if not level:
        return "подсборка не найдена — не исправлено"

    def change(source) -> str:
        source_top = cast(cast(source, "IKompasDocument3D").TopPart, "IPart7")
        return _section_in(source, fix.component, _children(source_top, cast), cast)

    return "; ".join(dict.fromkeys(_each_source(app, level, cast, change)))


# ── material ─────────────────────────────────────────────────────────────────


@dataclass
class MaterialFix:
    """Set the material of the detail *component* in the assembly at *path*."""

    component: Component
    path: list[Component] = field(default_factory=list)
    material: str = ""
    density: float = 0.0

    @property
    def assembly_title(self) -> str:
        return self.path[-1].title if self.path else ""

    @property
    def file(self) -> str:
        """The file that changes: the detail's own, or (a local detail) the file it lives in."""
        for c in reversed([*self.path, self.component]):
            if c.file and ">" not in c.file:
                return c.file
        return ""

    @property
    def text(self) -> str:
        where = f" (в «{self.assembly_title}»)" if self.path else ""
        return f"{self.component.title}{where}"


def material_fixes(top: Component) -> list[MaterialFix]:
    """The details without a material (or with the KOMPAS default «Сталь 10»); a detail file once."""
    out: list[MaterialFix] = []
    seen: set = set()

    def walk(assembly: Component, path: list[Component]) -> None:
        for child in assembly.children:
            if child.kind == DETAIL and no_material(child.material):
                local = ">" in child.file
                key = (tuple(c.title for c in path), child.marking, child.name) if local or not child.file \
                    else child.file.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(MaterialFix(child, list(path)))
            if child.kind == ASSEMBLY:
                walk(child, path + [child])

    walk(top, [])
    return out


def choose_kompas_material(hwnd: int = 0) -> tuple[str, float] | None:
    """The material from the «Материалы» library of KOMPAS (Полином): its window, the designer
    chooses a material or a sortament. (designation as KOMPAS keeps it, density g/cm³), or None."""
    try:
        from win32com.client import Dispatch
    except ImportError as exc:
        raise AssemblyError("Нужен пакет pywin32: pip install pywin32") from exc
    try:
        materials = Dispatch("Ascon.Polynom.Materials.Integrator").Materials
    except Exception as exc:
        raise AssemblyError(f"Справочник «Материалы» КОМПАС-3D недоступен: {exc}") from exc
    # Options: what may be chosen — ObjectClass Material | Sortament | SortamentEx
    chosen = materials.SelectObject("", 1 | 2 | 4, int(hwnd), 0)
    if chosen is None:
        return None
    designation = str(chosen.DisplayName or chosen.Designation or "").strip()
    try:
        density = float(str(chosen.GetProperty("Плотность").Value).replace(",", ".")) / 1000  # kg/m³ → g/cm³
    except Exception:
        density = 0.0
    return (designation, density) if designation else None


def _fix_material(app, doc, top, fix: MaterialFix, cast) -> str:
    level = _along(top, fix.path, cast)
    parts = [p for parent in level for p in _children(parent, cast) if _same(p, fix.component)]
    if not parts:
        return "деталь не найдена — не исправлено"

    def change(source) -> str:
        part = cast(cast(source, "IKompasDocument3D").TopPart, "IPart7")
        part.SetMaterial(fix.material, fix.density)
        part.Update()
        return "материал задан"

    return "; ".join(dict.fromkeys(_each_source(app, parts, cast, change)))
