# -*- coding: utf-8 -*-
"""Обход сборки КОМПАС: получение списка деталей."""

import os
import re
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

from dxfka.compat import CastTo, dynamic_dispatch
from dxfka.config import (
    MAX_ASSEMBLY_DEPTH, OPEN_RETRY_COUNT, OPEN_RETRY_WAIT_S)
from dxfka.kompas.connection import KompasConnection
from dxfka.models import PartInfo
from dxfka.utils import (
    com_count, com_items, is_standard_part_path, safe_int, safe_str)

# ======================================================================
# ОБХОД СБОРКИ: получение списка деталей
# ======================================================================

class AssemblyWalker:
    """
    Рекурсивный обход сборки .a3d с извлечением всех уникальных деталей .m3d.

    Проверенный путь kompas_sheet_export.py (TopPart.PartsEx) на реальной
    сборке v24 вернул пустую коллекцию, поэтому перебираем несколько
    стратегий доступа к компонентам и логируем результат каждой.
    TODO_KOMPAS: уточнить по документации КОМПАС v24 корректный способ
    перечисления компонентов (IParts7.PartsEx / Parts / Components).
    """

    def __init__(self, conn: KompasConnection,
                 log: Callable[[str, str], None],
                 cancel_event: Optional[threading.Event] = None):
        self.conn = conn
        self.log = log
        self.cancel_event = cancel_event
        self._local_seq = 0           # счётчик локальных деталей (для uid)
        self.source_assembly = ""     # файл сборки-источника (для PartInfo)

    # ------------------------------------------------------------------

    def _cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    def walk(self, assembly_path: str) -> List[PartInfo]:
        """Точка входа: возвращает список уникальных деталей сборки."""
        parts: List[PartInfo] = []
        seen = set()
        self._local_seq = 0
        self.source_assembly = os.path.abspath(assembly_path)
        self._walk_file(assembly_path, parts, seen, depth=0)
        self.log("info", f"Обход завершён. Найдено уникальных деталей: {len(parts)}")
        return parts

    def walk_top(self, top_part: Any, base_dir: str,
                 source_assembly: str) -> List[Tuple[PartInfo, Any]]:
        """
        Обход уже открытого TopPart (используется экспортёром локальных
        деталей). Возвращает пары (PartInfo, компонент IPart7) в том же
        порядке, что и при анализе сборки, — порядок детерминирован.
        """
        pairs: List[Tuple[PartInfo, Any]] = []
        self._local_seq = 0
        self.source_assembly = source_assembly
        self._walk_parts_inmemory(top_part, pairs, set(), set(), 1, base_dir)
        self.log("info", f"Повторный обход TopPart: найдено деталей {len(pairs)}")
        return pairs

    # ------------------------------------------------------------------

    def _open_document(self, path: str) -> Any:
        """Открытие документа с повторами (КОМПАС может быть занят)."""
        for attempt in range(1, OPEN_RETRY_COUNT + 1):
            if self._cancelled():
                return None
            try:
                doc = self.conn.application.Documents.Open(path)
                if doc is not None:
                    time.sleep(1.0)
                    return doc
            except Exception as exc:
                self.log("warning",
                         f"Открытие не удалось (попытка {attempt}): {exc}")
            self.conn.pump(OPEN_RETRY_WAIT_S)
        return None

    def _close_document(self, doc: Any) -> None:
        """Закрытие документа без сохранения (двойная попытка)."""
        try:
            doc.Close(False)
        except Exception:
            try:
                doc.Close()
            except Exception as exc:
                self.log("warning", f"Не удалось закрыть документ: {exc}")
        time.sleep(0.5)

    def _get_doc3d(self, doc: Any) -> Any:
        """IKompasDocument3D (для TopPart); fallback — сам документ."""
        if self.conn.api7_module is not None:
            try:
                return self.conn.api7_module.IKompasDocument3D(doc)
            except Exception as exc:
                self.log("warning", f"IKompasDocument3D: {exc}; динамический fallback")
        # Не ранняя обёртка IKompasDocument (в ней нет TopPart), а позднее
        # связывание по имени свойства.
        return dynamic_dispatch(doc)

    def get_top_part(self, doc: Any) -> Any:
        """
        TopPart сборки: IAssemblyDocument(doc).TopPart, fallback —
        IKompasDocument3D(doc).TopPart. None — получить не удалось.
        Общий код для обхода файла и выгрузки локальных деталей.
        """
        asm = None
        if self.conn.api7_module is not None:
            try:
                asm = self.conn.api7_module.IAssemblyDocument(doc)
            except Exception as exc:
                self.log("warning", f"IAssemblyDocument: {exc}")
        top_part = None
        if asm is not None:
            try:
                top_part = asm.TopPart
            except Exception as exc:
                self.log("warning", f"asm.TopPart: {exc}")
        if top_part is None:
            try:
                top_part = self._get_doc3d(doc).TopPart
            except Exception as exc:
                self.log("warning", f"doc3d.TopPart: {exc}")
        if top_part is None:
            try:
                top_part = dynamic_dispatch(doc).TopPart
            except Exception as exc:
                self.log("warning", f"TopPart (динамически): {exc}")
        return top_part

    # ------------------------------------------------------------------

    def _component_children(self, part: Any) -> List[Any]:
        """
        Компоненты узла: перебор стратегий доступа (Parts -> PartsEx ->
        Components); какая сработала — в лог (на v24 PartsEx бывал пуст).
        """
        getters = (
            ("Parts", lambda: part.Parts),
            ("PartsEx()", lambda: part.PartsEx()),
            ("PartsEx(1)", lambda: part.PartsEx(1)),
            ("Components", lambda: part.Components),
        )
        for label, getter in getters:
            try:
                collection = getter()
            except Exception as exc:
                if label == "Parts":
                    self.log("warning", f"part.Parts: {exc}")
                continue
            items = com_items(collection)
            if items:
                if label != "Parts":
                    self.log("info",
                             f"Компоненты получены через {label}: "
                             f"{len(items)} шт.")
                return items
        return []

    def _walk_parts_inmemory(self, part: Any,
                             pairs: List[Tuple[PartInfo, Any]],
                             refs: set, seen_paths: set,
                             depth: int, base_dir: str) -> None:
        """
        Рекурсивный обход компонентов сборки В ПАМЯТИ (проверено на v24,
        по образцу «Сводника»): part.Parts -> CastTo(IPart7) -> рекурсия
        по p7.Parts; подсборки с диска не открываются. В pairs попадают
        пары (PartInfo, компонент) — компонент нужен экспортёру локальных
        деталей. Дедупликация:
          refs       — по Reference компонента (защита от циклов дерева);
          seen_paths — по (путь файла, исполнение): разные исполнения
                       одного файла = отдельные записи;
          локальные детали (без своего файла) — по своему ключу.
        Путь файла: у IPart7 нет FilePath; свойства — PathName (обычно
        полный путь), FileName (имя файла), Path (каталог). Относительное
        имя достраиваем от Path либо от каталога сборки.
        TODO_KOMPAS: семантика PathName/FileName/Path подобрана эмпирически;
        при странностях смотреть лог строк «Компонент: ...».
        """
        if self._cancelled():
            return
        if depth > MAX_ASSEMBLY_DEPTH:
            self.log("warning", "Превышена глубина вложенности компонентов")
            return
        children = self._component_children(part)
        if not children:
            return

        for child in children:
            if self._cancelled():
                return
            self._process_component(child, pairs, refs, seen_paths,
                                    depth, base_dir)

    def _process_component(self, child: Any,
                           pairs: List[Tuple[PartInfo, Any]],
                           refs: set, seen_paths: set,
                           depth: int, base_dir: str) -> None:
        """Обработка одного компонента сборки (файлового или локального)."""
        try:
            p7 = CastTo(child, "IPart7")
        except Exception:
            p7 = child

        name = safe_str(p7, "Name") or "Без имени"
        ref = safe_str(p7, "Reference")
        if not ref:
            ref = f"{safe_str(p7, 'Marking')}|{name}|{depth}"
        if ref in refs:
            return
        refs.add(ref)

        marking = safe_str(p7, "Marking")
        path_name = safe_str(p7, "PathName")
        file_only = safe_str(p7, "FileName")
        dir_path = safe_str(p7, "Path")
        file_name = ""
        for cand in (path_name, file_only):
            if cand and os.path.splitext(cand)[1].lower() in (".a3d",
                                                              ".m3d"):
                file_name = cand
                break
        if not file_name:
            file_name = path_name or file_only
        if not file_name:
            self._process_local_component(p7, name, marking, pairs, refs,
                                          seen_paths, depth, base_dir)
            return
        if not os.path.isabs(file_name):
            file_name = os.path.join(dir_path or base_dir, file_name)
        child_ext = os.path.splitext(file_name)[1].lower()
        self.log("info", f"Компонент: {name} -> {file_name}")

        if child_ext == ".a3d":
            # Подсборка — рекурсия в памяти, без открытия с диска.
            self._walk_parts_inmemory(p7, pairs, refs, seen_paths,
                                      depth + 1, base_dir)
        elif child_ext == ".m3d":
            performance = self._component_performance(p7, name, marking)
            key = (os.path.normcase(os.path.abspath(file_name)), performance)
            if key in seen_paths:
                return
            seen_paths.add(key)
            if is_standard_part_path(file_name):
                self.log("info",
                         f"Пропущено стандартное изделие: "
                         f"{os.path.basename(file_name)}")
                return
            material = safe_str(p7, "Material")
            pairs.append((PartInfo(
                file_path=os.path.abspath(file_name),
                name=name,
                marking=marking,
                material=material,
                is_sheet=self._sheet_heuristic(material),
                performance=performance,
                source_assembly=self.source_assembly,
            ), p7))
        else:
            self.log("warning",
                     f"Компонент неизвестного типа пропущен: {file_name}")

    def _process_local_component(self, p7: Any, name: str, marking: str,
                                 pairs: List[Tuple[PartInfo, Any]],
                                 refs: set, seen_paths: set,
                                 depth: int, base_dir: str) -> None:
        """
        Локальный компонент (встроен в сборку, своего файла нет):
        подсборка — рекурсия; деталь — запись с is_local=True (экспорт
        выполняется выгрузкой во временный .m3d, см. PartExporter).
        """
        if self._is_local_subassembly(p7, name):
            self.log("info", f"Локальная подсборка — рекурсия: {name}")
            self._walk_parts_inmemory(p7, pairs, refs, seen_paths,
                                      depth + 1, base_dir)
            return
        performance = self._component_performance(p7, name, marking)
        key = (f"local|{marking}|{name}", performance)
        if key in seen_paths:
            return
        seen_paths.add(key)
        self._local_seq += 1
        material = safe_str(p7, "Material")
        self.log("info", f"Локальная деталь: {name} (#{self._local_seq})")
        pairs.append((PartInfo(
            file_path="",
            name=name,
            marking=marking,
            material=material,
            is_sheet=self._sheet_heuristic(material),
            performance=performance,
            is_local=True,
            local_seq=self._local_seq,
            source_assembly=self.source_assembly,
        ), p7))

    def _is_local_subassembly(self, part: Any, name: str) -> bool:
        """
        Локальный компонент — подсборка? Перебираем признаки, всё в
        try/except (TODO_KOMPAS: точное свойство API не подтверждено).
        """
        for attr in ("IsAssembly", "AssemblyContent"):
            value = safe_str(part, attr).strip().lower()
            if value and value not in ("0", "false", "no"):
                self.log("info",
                         f"«{name}»: {attr}={value} — считаю подсборкой")
                return True
            if value:
                return False
        try:
            if com_count(part.Parts):
                self.log("info",
                         f"«{name}»: есть вложенные компоненты — "
                         "считаю локальной подсборкой")
                return True
        except Exception:
            pass
        return False

    def _component_performance(self, p7: Any, name: str, marking: str) -> str:
        """
        Номер исполнения компонента: свойство API (перебор кандидатов)
        либо суффикс «-NN» в обозначении (конструкция по ЕСКД).
        TODO_KOMPAS: имя свойства исполнений в API7 не подтверждено —
        при первой проверке смотреть строки лога «Исполнение компонента».
        """
        for attr in ("Performance", "PerformanceNumber", "ExecutionNumber"):
            value = safe_str(p7, attr).strip()
            if value:
                self.log("info",
                         f"Исполнение компонента «{name}»: {value} "
                         f"(свойство {attr})")
                return value
        match = re.search(r"-(\d{2,3})\s*$", (marking or "").strip())
        if match:
            self.log("info",
                     f"Исполнение компонента «{name}»: {match.group(1)} "
                     "(по суффиксу обозначения)")
            return match.group(1)
        return ""

    def _sheet_heuristic(self, material: str) -> Optional[bool]:
        """
        Предварительная оценка «листовая?» по материалу (слово «лист»).
        Истинный тип определяет ISheetMetalBody при экспорте; звёздочка
        в GUI означает эвристику.
        """
        low = (material or "").lower()
        if not low:
            return None
        return "лист" in low

    # ------------------------------------------------------------------

    def _walk_file(self, path: str, parts: List[PartInfo],
                   seen: set, depth: int) -> None:
        """Рекурсивный обход файла (.a3d — сборка, .m3d — деталь)."""
        if self._cancelled():
            return
        if depth > MAX_ASSEMBLY_DEPTH:
            self.log("warning", f"Превышена глубина вложенности: {path}")
            return
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)

        self.log("info", f"Обход: {os.path.basename(path)}")
        doc = self._open_document(path)
        if doc is None:
            self.log("error", f"Не удалось открыть: {path}")
            return
        try:
            doc_type = safe_int(doc, "DocumentType", 0)
            self.log("info", f"Тип документа: {doc_type}")

            ext = os.path.splitext(path)[1].lower()
            if ext == ".m3d":
                # Пользователь указал отдельную деталь вместо сборки.
                parts.append(PartInfo(file_path=os.path.abspath(path)))
                return

            # Сборка: IAssemblyDocument -> TopPart -> компоненты.
            top_part = self.get_top_part(doc)
            if top_part is None:
                self.log("error", "Не удалось получить TopPart сборки")
                return

            base_dir = os.path.dirname(os.path.abspath(path))
            self.log("info", "Обход компонентов в памяти (IPart7.Parts)")
            pairs: List[Tuple[PartInfo, Any]] = []
            self._walk_parts_inmemory(top_part, pairs, set(), seen, 1,
                                      base_dir)
            parts.extend(info for info, _ in pairs)
        finally:
            self._close_document(doc)
