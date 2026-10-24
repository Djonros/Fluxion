# -*- coding: utf-8 -*-
"""Модель данных детали и исключения управления потоком экспорта."""

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from dxfka.utils import sanitize_filename

# ======================================================================
# МОДЕЛЬ ДАННЫХ ДЕТАЛИ
# ======================================================================

@dataclass
class PartInfo:
    """Сведения об одной детали для списка в GUI."""
    file_path: str
    name: str = ""                # наименование из сборки
    marking: str = ""             # обозначение из сборки
    material: str = ""            # материал
    is_sheet: Optional[bool] = None   # None — неизвестно (уточнится при экспорте)
    status: str = "PENDING"       # PENDING | OK | ERROR | SKIP
    message: str = ""
    performance: str = ""         # номер исполнения ("" — исполнений нет)
    is_local: bool = False        # True — локальная деталь (встроена в сборку)
    local_seq: int = 0            # порядковый номер локальной детали при обходе
    source_assembly: str = ""     # файл сборки-источника (для локальных деталей)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "name": self.name,
            "marking": self.marking,
            "material": self.material,
            "is_sheet": self.is_sheet,
            "status": self.status,
            "message": self.message,
            "performance": self.performance,
            "is_local": self.is_local,
            "local_seq": self.local_seq,
            "source_assembly": self.source_assembly,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PartInfo":
        return cls(
            file_path=data.get("file_path", ""),
            name=data.get("name", ""),
            marking=data.get("marking", ""),
            material=data.get("material", ""),
            is_sheet=data.get("is_sheet"),
            performance=data.get("performance", ""),
            is_local=bool(data.get("is_local", False)),
            local_seq=int(data.get("local_seq", 0)),
            source_assembly=data.get("source_assembly", ""),
        )

    @property
    def uid(self) -> str:
        """
        Уникальный ключ строки в GUI и сообщениях PART_UPDATE:
        путь файла + исполнение; для локальных деталей — синтетический.
        """
        if self.is_local:
            return (f"local|{self.marking}|{self.name}|"
                    f"{self.performance}|{self.local_seq}")
        return (os.path.normcase(os.path.abspath(self.file_path))
                + "|" + self.performance)


def build_dxf_basename(part: PartInfo) -> str:
    """
    Базовое имя DXF: «обозначение наименование», иначе имя исходного файла.
    Для исполнения добавляется суффикс «-NN», чтобы файлы вариантов
    одной детали не перезаписывали друг друга.
    """
    base = f"{part.marking} {part.name}".strip()
    if not base:
        if part.is_local:
            base = "Локальная деталь"
        else:
            base = os.path.splitext(os.path.basename(part.file_path))[0]
    if part.performance:
        base = f"{base} -{part.performance}"
    return sanitize_filename(base) or "part"


def part_type_text(is_sheet: Optional[bool]) -> str:
    """Текст колонки «тип» (звёздочка = тип определён по материалу, не по API)."""
    if is_sheet is None:
        return "?"
    return "листовая" if is_sheet else "нелистовая"


def part_file_label(part: PartInfo) -> str:
    """Текст колонки «Файл»: имя файла либо пометка локальной детали."""
    if part.is_local:
        return "(локальная)"
    return os.path.basename(part.file_path)


# ======================================================================
# ИСКЛЮЧЕНИЯ УПРАВЛЕНИЯ ПОТОКОМ ЭКСПОРТА
# ======================================================================

class KompasConnectionError(ConnectionError):
    """Не удалось подключиться к КОМПАС (не установлен / нет pywin32 / занят)."""


class CancelledByUser(Exception):
    """Пользователь нажал «Остановить» во время паузы/работы."""


class SkippedByUser(Exception):
    """Пользователь нажал «Пропустить деталь» на паузе."""
