"""AutoCAD through its COM API (pywin32): a DWG turned into DXF for cad/drawing.py.

The designer's file is never opened for writing: a copy of the DWG is opened
read-only in AutoCAD, saved as DXF into a temporary folder and closed without
saving. Without AutoCAD the free ODA File Converter does the same, if it is
installed. ``open_drawing`` reads any DWG or DXF into a ``Drawing``.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .drawing import Drawing, DrawingError, read

PROG_IDS = ("AutoCAD.Application",)
AC2013_DXF = 61  # AcSaveAsType: AutoCAD 2013 DXF, read by every AutoCAD since 2013 and by ezdxf
RPC_E_CALL_REJECTED = -2147418111  # AutoCAD is busy (a command, a dialog): try again
ODA_GLOBS = (r"C:\Program Files\ODA\ODAFileConverter*\ODAFileConverter.exe",
             r"C:\Program Files (x86)\ODA\ODAFileConverter*\ODAFileConverter.exe")


def _retry(call, *args, tries: int = 20, pause: float = 0.5):
    for n in range(tries):
        try:
            return call(*args)
        except Exception as exc:  # pywintypes.com_error
            code = exc.args[0] if getattr(exc, "args", None) else None
            if code != RPC_E_CALL_REJECTED or n == tries - 1:
                raise
            time.sleep(pause)


def connect(log=print):
    """The running AutoCAD, or a new one (it takes a minute to start)."""
    try:
        import win32com.client
    except ImportError as exc:
        raise DrawingError("Нет pywin32: работа с AutoCAD только в Windows (pip install pywin32).") from exc
    for prog_id in PROG_IDS:
        try:
            return win32com.client.GetActiveObject(prog_id)
        except Exception:
            pass
    log("Запускаю AutoCAD…")
    for prog_id in PROG_IDS:
        try:
            app = win32com.client.Dispatch(prog_id)
        except Exception:
            continue
        for _ in range(120):  # Dispatch returns before AutoCAD can take calls
            try:
                _ = app.Documents.Count
                return app
            except Exception:
                time.sleep(0.5)
        return app
    raise DrawingError("AutoCAD не найден: не удалось подключиться к «AutoCAD.Application».")


def _open_documents(app) -> list:
    try:
        return [app.Documents.Item(i) for i in range(app.Documents.Count)]
    except Exception:
        return []


def dwg_to_dxf_autocad(dwg: Path, folder: Path, log=print) -> Path:
    app = connect(log)
    for doc in _open_documents(app):
        try:
            if Path(doc.FullName).resolve() == dwg.resolve() and not doc.Saved:
                log("В AutoCAD есть несохранённые изменения этого чертежа: проверяю файл на диске.")
        except Exception:
            pass
    copy = folder / dwg.name
    shutil.copy2(dwg, copy)
    target = folder / (dwg.stem + ".dxf")
    log(f"Открываю {dwg.name} в AutoCAD (копию, только для чтения)…")
    doc = _retry(app.Documents.Open, str(copy), True)
    try:
        _retry(doc.SaveAs, str(target), AC2013_DXF)
    finally:
        try:
            _retry(doc.Close, False)
        except Exception:
            pass
    if not target.exists():
        raise DrawingError("AutoCAD не сохранил DXF.")
    return target


def oda_converter() -> str | None:
    found = os.environ.get("FLUXION_ODA") or ""
    if found and Path(found).exists():
        return found
    for pattern in ODA_GLOBS:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return shutil.which("ODAFileConverter")


def dwg_to_dxf_oda(dwg: Path, folder: Path, exe: str, log=print) -> Path:
    src, out = folder / "in", folder / "out"
    src.mkdir()
    out.mkdir()
    shutil.copy2(dwg, src / dwg.name)
    log("Перевожу DWG в DXF (ODA File Converter)…")
    subprocess.run([exe, str(src), str(out), "ACAD2013", "DXF", "0", "1", dwg.name],
                   check=False, timeout=300, capture_output=True)
    target = out / (dwg.stem + ".dxf")
    if not target.exists():
        raise DrawingError("ODA File Converter не перевёл чертёж в DXF.")
    return target


def open_drawing(path: str | Path, log=print, use_autocad: bool = True) -> Drawing:
    """A DWG (through AutoCAD, or ODA File Converter) or a DXF as a ``Drawing``."""
    path = Path(path)
    if not path.exists():
        raise DrawingError(f"Файл не найден: {path}")
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        log(f"Читаю {path.name}…")
        return read(path)
    if suffix != ".dwg":
        raise DrawingError("Нужен чертёж DWG или DXF.")
    with tempfile.TemporaryDirectory(prefix="fluxion-acad-") as tmp:
        folder = Path(tmp)
        errors = []
        dxf = None
        if use_autocad:
            try:
                dxf = dwg_to_dxf_autocad(path, folder, log)
            except DrawingError as exc:
                errors.append(str(exc))
            except Exception as exc:  # COM errors carry the AutoCAD message
                errors.append(f"AutoCAD: {exc}")
        if dxf is None:
            exe = oda_converter()
            if exe:
                dxf = dwg_to_dxf_oda(path, folder, exe, log)
        if dxf is None:
            raise DrawingError("Не удалось открыть DWG: " + ("; ".join(errors) + ". " if errors else "")
                               + "Нужен запущенный AutoCAD или бесплатный ODA File Converter. "
                               "Можно сохранить чертёж как DXF и проверить его.")
        log("Читаю чертёж…")
        return read(dxf, file=str(path))
