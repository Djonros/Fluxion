"""Коннектор «Папки»: что появилось, изменилось, переименовано и удалено в выбранных папках.

    settings = {"folders": ["C:\\Проекты\\792720", "D:\\Входящие"],
                "mask": "*.a3d;*.m3d;*.cdw;*.pdf",     # "" — все файлы
                "exclude": "~$*;*.tmp;*.bak;*.~*"}

Windows says about every change at once (watchdog, ReadDirectoryChangesW); without
watchdog the folders are compared every ``POLL`` seconds. A program saving a file
writes it several times (KOMPAS does) — the changes of one file are joined into one
event after ``QUIET`` seconds of silence: created + changed = created, created +
deleted = nothing. Only the path and the size are written, never what is inside.
"""
from __future__ import annotations

import fnmatch
import os
import threading
import time
from pathlib import Path

from .connector import Connector, Event

QUIET = 4.0
POLL = 10.0
DEFAULT_EXCLUDE = "~$*;*.tmp;*.bak;*.~*;*.lck;.~lock*;Thumbs.db;desktop.ini"
SKIP_DIRS = {".git", "__pycache__", ".fluxion-backup", "$RECYCLE.BIN", "System Volume Information"}

KINDS = {
    "file.created": "Новый файл",
    "file.modified": "Изменён файл",
    "file.deleted": "Удалён файл",
    "file.moved": "Переименован файл",
}


def masks(text: str) -> list[str]:
    return [m.strip().lower() for m in str(text or "").replace(",", ";").split(";") if m.strip()]


def _join(old: str, new: str) -> str | None:
    """Two changes of one file in a row → one; None when nothing is left."""
    if old == "file.created":
        return None if new == "file.deleted" else "file.created"
    if old == "file.deleted" and new == "file.created":
        return "file.modified"  # saved by "write a copy, delete, rename"
    return new


class FolderConnector(Connector):
    id = "folders"
    title = "Папки"
    description = "Что появилось и изменилось в выбранных папках: новые модели, чертежи, письма заказчику."
    reads = ("имена, размер и время изменения файлов в выбранных папках",)
    records = ("путь файла, что с ним случилось и когда",)
    never = ("не открывает и не читает содержимое файлов", "ничего не удаляет и не перемещает")

    def __init__(self, settings: dict | None = None):
        super().__init__(settings)
        self.folders = [str(f) for f in self.settings.get("folders") or [] if str(f).strip()]
        self.include = masks(self.settings.get("mask", ""))
        self.exclude = masks(self.settings.get("exclude", DEFAULT_EXCLUDE))
        self._pending: dict[str, list] = {}   # path -> [kind, last change, old path]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._observer = None

    # ── what counts ──
    def wanted(self, path: str) -> bool:
        name = os.path.basename(path).lower()
        if any(part in SKIP_DIRS for part in Path(path).parts):
            return False
        if any(fnmatch.fnmatch(name, m) for m in self.exclude):
            return False
        return not self.include or any(fnmatch.fnmatch(name, m) for m in self.include)

    # ── the changes, joined ──
    def raw(self, kind: str, path: str, old: str = "", now: float | None = None) -> None:
        """A change as Windows told it; becomes an event in ``flush`` after the quiet time."""
        if not self.wanted(path):
            return
        now = time.time() if now is None else now
        with self._lock:
            if kind == "file.moved" and old in self._pending:
                prev = self._pending.pop(old)
                kind = prev[0] if prev[0] == "file.created" else kind
                old = (prev[2] or old) if kind == "file.moved" else ""
            item = self._pending.get(path)
            if item is None:
                self._pending[path] = [kind, now, old]
                return
            joined = _join(item[0], kind)
            if joined is None:
                del self._pending[path]
            else:
                item[0], item[1] = joined, now
                item[2] = item[2] or old

    def flush(self, now: float | None = None, force: bool = False) -> list[Event]:
        now = time.time() if now is None else now
        ready = []
        with self._lock:
            for path, (kind, last, old) in list(self._pending.items()):
                if force or now - last >= QUIET:
                    del self._pending[path]
                    ready.append(self._event(kind, path, old, last))
        for event in sorted(ready, key=lambda e: e.time):
            self.emit(event)
        return ready

    def _event(self, kind: str, path: str, old: str, when: float) -> Event:
        name = os.path.basename(path)
        title = f"{KINDS[kind]}: {name}"
        detail = os.path.dirname(path)
        if kind == "file.moved" and old:
            title = f"{KINDS[kind]}: {os.path.basename(old)} → {name}"
            detail = f"было: {old}"
        elif kind != "file.deleted":
            try:
                size = os.path.getsize(path)
                detail = f"{detail}; {size / 1024:.0f} КБ" if size >= 1024 else f"{detail}; {size} Б"
            except OSError:
                pass
        return Event(self.id, kind, title, ref=path, detail=detail, time=when)

    # ── running ──
    def start(self, emit) -> None:
        super().start(emit)
        missing = [f for f in self.folders if not os.path.isdir(f)]
        if not self.folders:
            self.fail("не выбрано ни одной папки")
        elif missing:
            self.fail("нет папки: " + "; ".join(missing))
        self._stop.clear()
        live = [f for f in self.folders if os.path.isdir(f)]
        if not live:
            return
        if not self._start_watchdog(live):
            self._spawn(self._poll_loop, live)
        self._spawn(self._flush_loop)

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(5)
            except Exception:
                pass
            self._observer = None
        for t in self._threads:
            t.join(5)
        self._threads = []
        self.flush(force=True)
        super().stop()

    def _spawn(self, target, *args) -> None:
        t = threading.Thread(target=target, args=args, daemon=True, name=f"fluxion-assistant-{self.id}")
        t.start()
        self._threads.append(t)

    def _flush_loop(self) -> None:
        while not self._stop.wait(1.0):
            self.flush()

    def _start_watchdog(self, folders: list[str]) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return False
        owner = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                kind = {"created": "file.created", "modified": "file.modified", "deleted": "file.deleted",
                        "moved": "file.moved"}.get(event.event_type)
                if kind == "file.moved":
                    owner.raw(kind, os.fsdecode(event.dest_path), os.fsdecode(event.src_path))
                elif kind:
                    owner.raw(kind, os.fsdecode(event.src_path))

        try:
            observer = Observer()
            for folder in folders:
                observer.schedule(Handler(), folder, recursive=True)
            observer.start()
        except Exception:
            return False
        self._observer = observer
        return True

    # ── without watchdog: compare the folders now and then ──
    def snapshot(self, folders: list[str]) -> dict[str, tuple[float, int]]:
        seen: dict[str, tuple[float, int]] = {}
        stack = list(folders)
        while stack and not self._stop.is_set():
            try:
                with os.scandir(stack.pop()) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name not in SKIP_DIRS:
                                    stack.append(entry.path)
                            elif self.wanted(entry.path):
                                st = entry.stat()
                                seen[entry.path] = (st.st_mtime, st.st_size)
                        except OSError:
                            continue
            except OSError:
                continue
        return seen

    def compare(self, before: dict, after: dict) -> None:
        for path, stamp in after.items():
            if path not in before:
                self.raw("file.created", path)
            elif before[path] != stamp:
                self.raw("file.modified", path)
        for path in before.keys() - after.keys():
            self.raw("file.deleted", path)

    def _poll_loop(self, folders: list[str]) -> None:
        before = self.snapshot(folders)
        while not self._stop.wait(POLL):
            after = self.snapshot(folders)
            if not self._stop.is_set():
                self.compare(before, after)
                before = after
