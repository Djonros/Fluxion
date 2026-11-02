"""The long jobs a bot runs in KOMPAS-3D, without the window: the assembly report,
«Пакет на лазер», «Сравнить версии», «Заявка на закупку».

Every job returns ``Result``: the text of the answer and the files to send. KOMPAS
does one job at a time — ``KOMPAS_LOCK``; COM is set up in the calling thread.
A job never asks: the warnings of a report are left unmarked (the workbook says
«не проверено»), the details DXFka would stop at are skipped and listed.
"""
from __future__ import annotations

import queue as queue_module
import threading
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .config import allowed_file

KOMPAS_LOCK = threading.Lock()
MODEL_SUFFIXES = (".a3d",)
VERSION_SUFFIXES = (".a3d", ".json")
SEARCH_LIMIT = 20000  # files looked at when a model is named, not given by path
LASER_TIMEOUT = 3 * 60 * 60


class JobError(RuntimeError):
    """Said to the user as it is."""


@dataclass
class Result:
    text: str
    files: list[Path] = field(default_factory=list)


@contextmanager
def com():
    try:
        import pythoncom

        pythoncom.CoInitialize()  # COM in a background thread
    except ImportError:  # not Windows: assembly.read explains that pywin32 is missing
        pythoncom = None
    try:
        yield
    finally:
        if pythoncom is not None:
            pythoncom.CoUninitialize()


def find_model(query: str, folders: list[str], suffixes=MODEL_SUFFIXES) -> Path:
    """The file meant by *query*: a full path inside the allowed folders, or a name
    (with or without the extension) looked up in them; the newest when several."""
    query = query.strip().strip('"«»')
    if not query:
        raise JobError("Укажите файл: путь к нему или имя, например «/отчёт Рама 600.000».")
    if not folders:
        raise JobError("Папки для бота не заданы: Связь → «Боты в мессенджерах…» → «Папки».")
    path = Path(query)
    if path.is_absolute():
        if not allowed_file(path, folders):
            raise JobError("Этот файл вне папок, открытых для бота.")
        if not path.is_file():
            raise JobError(f"Файла нет: {path}")
        return path
    wanted = query.lower()
    exact, partial = [], []
    seen = 0
    for folder in folders:
        root = Path(folder)
        if not root.is_dir():
            continue
        for file in root.rglob("*"):
            seen += 1
            if seen > SEARCH_LIMIT:
                break
            if file.suffix.lower() not in suffixes or not file.is_file():
                continue
            name, stem = file.name.lower(), file.stem.lower()
            if wanted in (name, stem):
                exact.append(file)
            elif wanted in name:
                partial.append(file)
    found = exact or partial
    if not found:
        raise JobError(f"Не нашёл «{query}» в папках бота.")
    if not exact and len(partial) > 1:
        names = ", ".join(sorted({f.name for f in partial})[:8])
        raise JobError(f"Под «{query}» подходят несколько: {names}. Уточните имя.")
    return max(found, key=lambda f: f.stat().st_mtime)


@contextmanager
def kompas(log):
    if not KOMPAS_LOCK.acquire(blocking=False):
        log("КОМПАС занят другим заданием — жду очереди…")
        KOMPAS_LOCK.acquire()
    try:
        with com():
            yield
    finally:
        KOMPAS_LOCK.release()


def report(path: Path, log=print, estimate_only: bool = False) -> Result:
    """«Ведомость и металлоёмкость сборки»: the workbook and its summary (and the snapshot for comparisons)."""
    from cad import assembly
    from cad import compare
    from cad import report as reports

    with kompas(log):
        try:
            top = assembly.read(path, log=log)
        except assembly.AssemblyError as exc:
            raise JobError(str(exc)) from exc
        result = reports.build(top, reports.load_decisions(top.file))
        reports.add_welds(result, log=log)
    target = reports.save_xlsx(result, reports.default_path(top), source=Path(path).name)
    cost = None
    try:
        from cad import estimate

        est = estimate.build(result)
        cost = est.total
        line = estimate.summary_line(est)
    except Exception:  # unreadable norms: the report without the cost
        line = ""
    try:
        compare.save_snapshot(result, target, cost)
    except OSError:
        pass
    text = (line or reports.summary_text(result, target)) if estimate_only else reports.summary_text(result, target)
    if result.unchecked:
        text += (f"\n\nНе отмечено предупреждений: {len(result.unchecked)} — в отчёте они «не проверены». "
                 "Отметить: в программе Производство → «Ведомость и металлоёмкость сборки…».")
    return Result(text, [target])


def compare_versions(old: Path, new: Path, log=print) -> Result:
    from cad import compare

    with kompas(log):
        try:
            diff = compare.compare(compare.version_snapshot(str(old), log), compare.version_snapshot(str(new), log))
        except ValueError as exc:
            raise JobError(str(exc)) from exc
    path = compare.save_xlsx(diff, compare.default_path(diff))
    return Result(compare.summary_text(diff), [Path(path)])


def purchase(products: list[tuple[Path, int]], log=print) -> Result:
    from cad import compare
    from cad import purchase as purchases

    with kompas(log):
        snapshots = [(compare.version_snapshot(str(path), log), qty) for path, qty in products]
    order = purchases.build(snapshots)
    path = purchases.save_xlsx(order, purchases.default_path())
    return Result(purchases.summary_text(order), [Path(path)])


def zip_folder(folder: Path, target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(Path(folder).rglob("*")):
            if file.is_file() and file != target:
                archive.write(file, file.relative_to(folder))
    return target


def laser(path: Path, log=print, job_factory=None, timeout: float = LASER_TIMEOUT) -> Result:
    """«Пакет на лазер» in the automatic mode: DXF by thickness, zipped; what needs a
    hand in KOMPAS is skipped and named."""
    from cad.laser import LaserJob, default_out_dir, thickness_summary

    out_dir = default_out_dir(path)
    skipped: list[str] = []
    with kompas(log):
        job = (job_factory or LaserJob)(str(path), str(out_dir), auto=True)
        job.start()
        deadline = time.monotonic() + timeout
        final = ""
        while not final:
            if time.monotonic() > deadline:
                job.cancel()
                raise JobError("Пакет на лазер не уложился в отведённое время и остановлен.")
            try:
                msg = job.queue.get(timeout=0.5)
            except queue_module.Empty:
                continue
            kind = msg.get("type")
            if kind == "TRAVERSAL_DONE":
                count = job.export(msg.get("parts") or [])
                log(f"На лазер (из листа): {count} дет.")
                if not count:
                    return Result("Листовых деталей в сборке нет — DXF не нужен.")
            elif kind == "PAUSE":  # a manual step: nobody at the screen
                skipped.append(msg.get("title") or "деталь")
                job.skip()
            elif kind == "ASK_THICKNESS":
                skipped.append(f"{msg.get('part') or 'деталь'} — толщина не определена")
                job.answer_thickness("")
            elif kind == "DONE":
                final = msg.get("text") or "Экспорт завершён."
            elif kind == "FATAL":
                raise JobError(msg.get("text") or "Ошибка DXFka.")
    summary = thickness_summary(out_dir)
    lines = [final]
    files = []
    if summary:
        lines.append("По толщинам: " + "; ".join(f"{name} мм — {count} DXF" for name, count in summary) + ".")
        lines.append(f"Папка: {out_dir}")
        files.append(zip_folder(out_dir, out_dir / f"{Path(path).stem} - DXF.zip"))
    if skipped:
        lines.append(f"Пропущено (нужна рука в КОМПАС): {len(skipped)} — " + "; ".join(skipped[:10])
                     + ("…" if len(skipped) > 10 else "") + ". Их сделайте из программы: Производство → «Пакет на лазер (DXF)…».")
    return Result("\n".join(lines), files)
