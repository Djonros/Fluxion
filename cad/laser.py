"""«Пакет на лазер»: DXF of the sheet details of an assembly, by thickness — the
core of DXFka (package dxfka) driven by Fluxion.

``LaserJob`` runs the DXFka worker in two steps: the walk of the assembly, then
the export of the details that are cut from a sheet (a sheet-metal body, or a
plate whose material is a sheet: "Лист$d8 ГОСТ …"). Messages of the worker come
through ``job.queue`` (LOG, STATUS, PROGRESS, PAUSE, ASK_THICKNESS,
TRAVERSAL_DONE, PART_UPDATE, DONE, FATAL); the window answers a PAUSE with
``resume``/``skip``/``cancel`` and a thickness question with ``answer_thickness``.
The licence is the Pro licence of Fluxion.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path

PRO_FEATURE = "laser_dxf"


class ProLicense:
    """The licence the DXFka worker asks before every detail: Fluxion Pro."""

    def reserve(self) -> bool:
        try:
            from licensing import feature_enabled
        except Exception:
            return False
        return feature_enabled(PRO_FEATURE)

    def commit(self) -> None:
        pass

    def release(self) -> None:
        pass


def _logger() -> logging.Logger:
    from dxfka.config import LOG_FILE
    from dxfka.utils import setup_logging

    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    return setup_logging()


def cut_from_sheet(part) -> bool:
    """A detail for the laser: a sheet-metal body, or a material given as a sheet."""
    from dxfka.utils import thickness_from_material

    return bool(part.is_sheet) or bool(thickness_from_material(part.material or ""))


def default_out_dir(assembly: str | Path) -> Path:
    """The "DXF" folder next to the model folder when the project has one, else Документы\\Fluxion CAD."""
    assembly = Path(assembly)
    for candidate in (assembly.parent / "DXF", assembly.parent.parent / "DXF"):
        if candidate.is_dir():
            return candidate / assembly.stem
    from .kompas import default_save_dir

    return default_save_dir() / f"{assembly.stem} - DXF"


def thickness_summary(out_dir: str | Path) -> list[tuple[str, int]]:
    """(thickness folder, number of DXF) of a finished package."""
    out = []
    root = Path(out_dir)
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            count = len(list(folder.glob("*.dxf")))
            if count:
                out.append((folder.name, count))
    return out


class LaserJob:
    """One package: walk, then export; the caller reads ``queue``."""

    def __init__(self, assembly: str | Path, out_dir: str | Path, auto: bool = True, template: str | None = None):
        self.assembly = str(assembly)
        self.out_dir = str(out_dir)
        self.auto = auto
        self.template = template
        self.queue: queue.Queue = queue.Queue()
        self.resume_event = threading.Event()
        self.cancel_event = threading.Event()
        self.skip_event = threading.Event()
        self.worker = None
        self.parts: list = []  # the details being exported
        self.left_out: list = []  # not cut from a sheet

    def _worker(self, mode: str, payload):
        from dxfka.worker import ExportWorker

        worker = ExportWorker(self.queue, self.resume_event, self.cancel_event, self.skip_event,
                              mode, payload, ProLicense(), _logger())
        worker.auto_mode = self.auto
        worker.template_path = self.template
        self.worker = worker
        worker.start()
        return worker

    def start(self) -> None:
        """Step 1: the walk of the assembly (TRAVERSAL_DONE comes with the details)."""
        self._worker("traverse", self.assembly)

    def export(self, parts_data: list[dict]) -> int:
        """Step 2: the export of the details cut from a sheet; returns how many."""
        from dxfka.models import PartInfo

        parts = [PartInfo.from_dict(d) for d in parts_data]
        self.parts = [p for p in parts if cut_from_sheet(p)]
        self.left_out = [p for p in parts if not cut_from_sheet(p)]
        if not self.parts:
            return 0
        os.makedirs(self.out_dir, exist_ok=True)
        self._worker("export", (self.out_dir, self.parts))
        return len(self.parts)

    # the window's answers to the worker
    def resume(self) -> None:
        self.resume_event.set()

    def skip(self) -> None:
        self.skip_event.set()
        self.resume_event.set()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.resume_event.set()
        if self.worker is not None:
            self.worker.thickness_event.set()

    def answer_thickness(self, thickness: str | None) -> None:
        """A thickness in mm, "" to skip the detail, None to stop."""
        if self.worker is None:
            return
        if thickness is None:
            self.worker.thickness_result = ("cancel", "")
        elif not thickness.strip():
            self.worker.thickness_result = ("skip", "")
        else:
            self.worker.thickness_result = ("ok", thickness.strip().replace(",", "."))
        self.worker.thickness_event.set()

    def running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()
