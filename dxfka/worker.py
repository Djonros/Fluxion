# -*- coding: utf-8 -*-
"""Рабочий поток: весь COM выполняется только здесь."""

import logging
import os
import queue
import shutil
import tempfile
import threading
import traceback
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from dxfka.compat import PYWIN32_OK, pythoncom
from dxfka.config import LICENSE_CONTACT_HINT
from dxfka.kompas.connection import KompasConnection
from dxfka.kompas.exporter import PartExporter
from dxfka.kompas.walker import AssemblyWalker
from typing import Protocol


class LicenseManager(Protocol):
    """What the worker needs of a licence (Fluxion passes its Pro check)."""

    def reserve(self) -> bool: ...

    def commit(self) -> None: ...

    def release(self) -> None: ...

from dxfka.models import (
    CancelledByUser, KompasConnectionError, PartInfo, SkippedByUser,
    build_dxf_basename)
from dxfka.utils import sanitize_filename

# ======================================================================
# РАБОЧИЙ ПОТОК (весь COM — только здесь)
# ======================================================================

class ExportWorker(threading.Thread):
    """
    Фоновый поток для работы с КОМПАС (обход сборки или экспорт деталей).
    GUI общается с потоком через:
      * msg_queue  — сообщения поток -> GUI (LOG/STATUS/PROGRESS/PAUSE/…);
      * события    — GUI -> поток (resume_event «Продолжить»,
                     skip_event «Пропустить деталь», cancel_event «Остановить»).
    Примечание: активный COM-вызов прервать нельзя — «Остановить» срабатывает
    после завершения текущего шага (открытие/построение/сохранение).
    """

    def __init__(self, msg_queue: "queue.Queue",
                 resume_event: threading.Event,
                 cancel_event: threading.Event,
                 skip_event: threading.Event,
                 mode: str,                       # "traverse" | "export"
                 payload: Any,                    # путь сборки | (папка DXF, [PartInfo])
                 lic: LicenseManager,
                 logger: logging.Logger):
        super().__init__(daemon=True)
        self.msg_queue = msg_queue
        self.resume_event = resume_event
        self.cancel_event = cancel_event
        self.skip_event = skip_event
        self.mode = mode
        self.payload = payload
        self.lic = lic
        self.logger = logger
        self.template_path: Optional[str] = None   # шаблон фрагмента (.frw)
        # Автоматический режим: толщина берётся из листового тела, паузы
        # пропускаются; деталь, для которой это невозможно, — как вручную.
        self.auto_mode = False
        # Диалог толщины: GUI кладёт результат и будит событие.
        self.thickness_event = threading.Event()
        self.thickness_result: Optional[Tuple[str, str]] = None
        self._last_thickness = ""                  # подставляется по умолчанию

    # ---------------------- сообщения GUI ----------------------

    def _log(self, level: str, text: str) -> None:
        """Запись в файл-журнал и дублирование в GUI."""
        getattr(self.logger, level if level in ("info", "warning", "error")
                else "info")(text)
        self.msg_queue.put({"type": "LOG", "level": level, "text": text})

    def _status(self, text: str) -> None:
        self.msg_queue.put({"type": "STATUS", "text": text})

    # ---------------------- пауза ----------------------

    def _pause(self, title: str, instruction: str) -> None:
        """Блокирующая пауза до «Продолжить»; бросает Cancelled/Skipped."""
        self.skip_event.clear()
        self.resume_event.clear()
        self._status(title)
        self.msg_queue.put({"type": "PAUSE",
                            "title": title, "instruction": instruction})
        while not self.resume_event.wait(0.2):
            if self.cancel_event.is_set():
                raise CancelledByUser()
        if self.skip_event.is_set():
            raise SkippedByUser()

    def _ask_thickness(self, part: PartInfo) -> Tuple[str, str]:
        """
        Запрос толщины детали у пользователя (диалог в GUI-потоке).
        Возвращает ("ok", толщина_мм) | ("skip", "") | ("cancel", "").
        GUI кладёт результат в self.thickness_result и будит
        self.thickness_event; «Остановить» в GUI также будит cancel_event.
        """
        self.thickness_result = None
        self.thickness_event.clear()
        self._status("Укажите толщину детали…")
        self.msg_queue.put({"type": "ASK_THICKNESS",
                            "part": part.name
                            or os.path.basename(part.file_path),
                            "default": self._last_thickness})
        while not self.thickness_event.wait(0.2):
            if self.cancel_event.is_set():
                return "cancel", ""
        result = self.thickness_result or ("cancel", "")
        if result[0] == "ok":
            self._last_thickness = result[1]
        return result

    def _unique_dxf_name(self, part: PartInfo, part_dir: str,
                         used_names: Dict[str, set]) -> str:
        """Уникальное имя DXF внутри папки толщины: «база», «база (2)», …"""
        taken = used_names.setdefault(os.path.normcase(part_dir), set())
        base = build_dxf_basename(part)
        name = base
        suffix = 2
        while name.lower() in taken:
            name = f"{base} ({suffix})"
            suffix += 1
        taken.add(name.lower())
        return name

    def _make_dxf_path(self, part: PartInfo, out_dir: str, thickness: str,
                       used_names: Dict[str, set]) -> str:
        r"""Папка «<DXF>\<толщина>» (создаётся) + уникальное имя -> путь DXF."""
        part_dir = os.path.join(out_dir, sanitize_filename(thickness))
        os.makedirs(part_dir, exist_ok=True)
        name = self._unique_dxf_name(part, part_dir, used_names)
        path = os.path.join(part_dir, name + ".dxf")
        self._log("info", f"Толщина {thickness} мм -> папка: {part_dir}")
        return path

    # ---------------------- главный цикл ----------------------

    def run(self) -> None:
        tmp_dir: Optional[str] = None
        co_initialized = False
        try:
            if not PYWIN32_OK:
                raise KompasConnectionError(
                    "Не установлен пакет pywin32. Выполните: pip install pywin32")

            # COM инициализируется ВНУТРИ рабочего потока.
            pythoncom.CoInitialize()
            co_initialized = True

            self._log("info", "Подключаюсь к КОМПАС-3D…")
            conn = KompasConnection(self._log)
            conn.connect()
            self._log("info", "КОМПАС подключён ("
                      + ("запущен программой" if conn.started_by_us
                         else "подключено к работающему экземпляру") + ")")

            if self.mode == "traverse":
                # ---------- анализ сборки ----------
                walker = AssemblyWalker(conn, self._log, self.cancel_event)
                parts = walker.walk(str(self.payload))
                self.msg_queue.put(
                    {"type": "TRAVERSAL_DONE",
                     "parts": [p.to_dict() for p in parts]})
                return

            # ---------- экспорт выбранных деталей ----------
            out_dir, selected_parts = self.payload
            parts_list: List[PartInfo] = list(selected_parts)
            tmp_dir = tempfile.mkdtemp(prefix="kompas_dxf_")
            self._log("info", f"Временный каталог: {tmp_dir}")
            exporter = PartExporter(conn, self._log, tmp_dir,
                                    self.template_path)
            exporter.auto_mode = self.auto_mode

            # Локальные детали: предварительная выгрузка из сборок в temp
            # (сборка открывается один раз на группу, затем обычный конвейер).
            local_by_asm: Dict[str, List[PartInfo]] = {}
            for part in parts_list:
                if part.is_local:
                    local_by_asm.setdefault(
                        part.source_assembly or "", []).append(part)
            local_resolved: Dict[str, str] = {}
            for asm_path, locals_ in local_by_asm.items():
                if not asm_path:
                    for part in locals_:
                        self._log("error",
                                  f"Локальная деталь без сборки-источника: "
                                  f"{part.name}")
                    continue
                self._status("Выгрузка локальных деталей из сборки "
                             f"({len(locals_)} шт.)…")
                local_resolved.update(
                    exporter.export_local_parts(asm_path, locals_))

            total = len(parts_list)
            ok = err = skip = 0
            cancelled = False
            stopped_by_limit = False
            had_errors = False
            used_names: Dict[str, set] = {}   # папка толщины -> занятые имена
            self.msg_queue.put({"type": "PROGRESS", "done": 0, "total": total})

            for index, part in enumerate(parts_list, start=1):
                if self.cancel_event.is_set():
                    cancelled = True
                    break
                self._status(f"[{index}/{total}] "
                             f"{part.name or os.path.basename(part.file_path)}")
                self.msg_queue.put({"type": "PART_START", "uid": part.uid,
                                    "index": index, "total": total})

                # Лицензия/триал: резерв слота до начала работы с деталью.
                if not self.lic.reserve():
                    self._log("warning",
                              "Лимит пробной версии исчерпан — экспорт "
                              "остановлен. " + LICENSE_CONTACT_HINT)
                    stopped_by_limit = True
                    break

                # Путь DXF известен только после первой паузы детали
                # (толщина задаёт подпапку), поэтому передаём «фабрику» пути,
                # а толщина запрашивается сразу после первой паузы.
                box: Dict[str, str] = {"path": "", "thickness": ""}
                first_pause: Dict[str, bool] = {"done": False}
                # В автоматическом режиме деталь переходит на ручную
                # обработку, если толщину из модели получить не удалось.
                manual_part: Dict[str, bool] = {"on": not self.auto_mode}

                def dxf_factory() -> str:
                    if not box["path"]:
                        raise RuntimeError(
                            "Толщина не задана — путь DXF не определён")
                    return box["path"]

                def part_pause(title: str, instruction: str) -> None:
                    """Первая пауза детали: вид для резки + запрос толщины."""
                    if not manual_part["on"]:
                        thickness = exporter.detected_thickness
                        if box["path"]:
                            return          # авто: паузы после первой не нужны
                        if thickness:
                            first_pause["done"] = True
                            box["path"] = self._make_dxf_path(
                                part, out_dir, thickness, used_names)
                            box["thickness"] = thickness
                            return
                        manual_part["on"] = True
                        self._log("warning",
                                  "Толщина не определена автоматически — "
                                  "деталь обрабатывается вручную")
                    if not first_pause["done"]:
                        first_pause["done"] = True
                        instruction = (
                            "Определите нужный вид для резки, а также "
                            "толщину детали —\nона задаёт подпапку, в которую "
                            "будет сохранён DXF.\n\n" + instruction)
                    self._pause(title, instruction)
                    if not box["path"]:
                        action, thickness = self._ask_thickness(part)
                        if action == "skip":
                            raise SkippedByUser()
                        if action == "cancel":
                            raise CancelledByUser()
                        box["path"] = self._make_dxf_path(
                            part, out_dir, thickness, used_names)
                        box["thickness"] = thickness

                status, message = "ERROR", ""
                try:
                    if part.is_local and part.uid not in local_resolved:
                        status, message = "ERROR", (
                            "Не удалось выгрузить локальную деталь "
                            "из сборки (подробности в журнале)")
                    elif part.is_local:
                        # Локальная деталь экспортируется из временного .m3d.
                        status, message = exporter.export_part(
                            replace(part,
                                    file_path=local_resolved[part.uid]),
                            dxf_factory, part_pause)
                    else:
                        status, message = exporter.export_part(
                            part, dxf_factory, part_pause)
                    if status == "OK":
                        self.lic.commit()      # успешный экспорт: счётчик +1
                    else:
                        self.lic.release()
                        had_errors = True
                except SkippedByUser:
                    self.lic.release()
                    status, message = "SKIP", "Пропущено пользователем"
                except CancelledByUser:
                    self.lic.release()
                    cancelled = True
                    self._status("Остановлено пользователем")
                    break
                except Exception as exc:  # непредвиденное — лог и дальше
                    self.lic.release()
                    self.logger.error(traceback.format_exc())
                    self._log("error", f"Непредвиденная ошибка: {exc}")
                    status, message = "ERROR", f"Непредвиденная ошибка: {exc}"
                    had_errors = True

                if status == "OK":
                    ok += 1
                    self._log("info", f"Результат: OK — {message}")
                elif status == "SKIP":
                    skip += 1
                    self._log("warning", f"Результат: SKIP — {message}")
                else:
                    err += 1
                    self._log("error", f"Результат: ERROR — {message}")

                self.msg_queue.put({"type": "PART_UPDATE",
                                    "uid": part.uid,
                                    "status": status,
                                    "message": message,
                                    "is_sheet": part.is_sheet,
                                    "thickness": box["thickness"]})
                self.msg_queue.put({"type": "PROGRESS",
                                    "done": index, "total": total})

            lines = ["Экспорт завершён.",
                     f"Успешно: {ok}",
                     f"С ошибками: {err}",
                     f"Пропущено: {skip}"]
            if cancelled:
                lines.append("Остановлено пользователем.")
            if stopped_by_limit:
                lines.append("Остановлено: лимит пробной версии исчерпан.\n"
                             + LICENSE_CONTACT_HINT)
            self._log("info", "\n".join(lines))
            self.msg_queue.put({"type": "DONE", "ok": ok, "err": err,
                                "skip": skip, "cancelled": cancelled,
                                "stopped_by_limit": stopped_by_limit,
                                "text": "\n".join(lines)})

            # Временные файлы: чистим при отсутствии ошибок, иначе оставляем
            # для диагностики (пути записаны в журнал).
            if tmp_dir and os.path.isdir(tmp_dir):
                if not had_errors:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    tmp_dir = None

        except KompasConnectionError as exc:
            self.logger.error(traceback.format_exc())
            self._log("error", str(exc))
            self.msg_queue.put({"type": "FATAL", "text": str(exc)})
        except Exception as exc:
            self.logger.error(traceback.format_exc())
            self.msg_queue.put({"type": "FATAL",
                                "text": f"Критическая ошибка: {exc}"})
        finally:
            if tmp_dir and os.path.isdir(tmp_dir):
                self._log("info",
                          f"Временные файлы (диагностика) сохранены: {tmp_dir}")
            if co_initialized:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
