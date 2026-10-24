# -*- coding: utf-8 -*-
"""Подключение к КОМПАС и сброс кеша COM-обёрток."""

import os
import shutil
import sys
import tempfile
import time
from typing import Callable, List

from dxfka.compat import (
    Dispatch, GetActiveObject, PYWIN32_OK, gencache, pythoncom)
from dxfka.config import (
    KO_ASSOCIATION_VIEW_PARAM, KO_DOCUMENT_PARAM, PROGIDS_API7,
    PROGID_API5, TYPELIB_API5, TYPELIB_API7, TYPELIB_CONSTANTS)
from dxfka.models import KompasConnectionError

# ======================================================================
# СБРОС КЕША COM-ОБОЁРТОК PYWIN32 (win32com.gen_py)
# ======================================================================

def clear_com_cache() -> List[str]:
    """
    Удалить кеш COM-обёрток pywin32: каталог win32com.gen_py на диске
    (site-packages\\win32com\\gen_py и/или %TEMP%\\gen_py) и загруженные
    модули из памяти процесса.

    Лечит ошибки вида «module 'win32com.gen_py.…' has no attribute
    'IAssemblyDocument' / 'IKompasDocument3D'», которые возникают при
    устаревшем кеше после обновления/переустановки КОМПАС-3D. Кеш
    пересоздаётся автоматически при следующем подключении к КОМПАС.

    Вызывать только когда экспорт не выполняется. Возвращает список
    удалённых каталогов.
    """
    dirs = set()
    try:
        import win32com
        gen_path = getattr(win32com, "__gen_path__", None)
        if gen_path and os.path.isdir(gen_path):
            dirs.add(os.path.normpath(gen_path))
    except Exception:
        pass
    tmp_gen = os.path.normpath(os.path.join(tempfile.gettempdir(), "gen_py"))
    if os.path.isdir(tmp_gen):
        dirs.add(tmp_gen)

    removed: List[str] = []
    for path in dirs:
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.isdir(path):
            removed.append(path)

    # Выгружаем сгенерированные модули, но НЕ сам пакет win32com.gen_py:
    # без него кеш нельзя пересоздать в этом же процессе
    # («No module named 'win32com.gen_py'»).
    for name in [n for n in list(sys.modules)
                 if n.startswith("win32com.gen_py.")]:
        del sys.modules[name]

    # Пустой каталог пакета gen_py — чтобы следующий EnsureModule
    # сгенерировал обёртки заново.
    try:
        gen_path = gencache.GetGeneratePath()
        init_py = os.path.join(gen_path, "__init__.py")
        if not os.path.isfile(init_py):
            open(init_py, "w").close()
    except Exception:
        pass

    try:
        for attr in ("loadedInfo", "loaded_info"):
            state = getattr(gencache, attr, None)
            if isinstance(state, dict):
                state.clear()
        gencache.__init__()     # перечитать (пустой) индекс кеша
    except Exception:
        pass

    return removed


# ======================================================================
# ПОДКЛЮЧЕНИЕ К КОМПАС (выполняется ТОЛЬКО в рабочем потоке)
# ======================================================================

class KompasConnection:
    """
    Подключение к КОМПАС-3D: API5 (KompasObject, 2D-документы/чертежи)
    и API7 (IApplication, 3D-модели/сборки).

    Проверенная схема (batch_export_dxf_v3.py): раннее связывание через
    gencache.EnsureModule(GUID, 0, 1, 0) + QueryInterface к нужному
    интерфейсу. GetActiveObject подключается к работающему КОМПАС,
    Dispatch запускает новый экземпляр.
    """

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self.constants = None        # модуль констант ks*/ko*
        self.api5_module = None      # ранние обёртки API5
        self.api7_module = None      # ранние обёртки API7
        self.kompas_object = None    # API5: KompasObject
        self.application = None      # API7: IApplication
        self.started_by_us = False   # запустили ли КОМПАС сами
        # Числовые константы (fallback, если typelib не загрузилась):
        self.KO_DOCUMENT_PARAM = KO_DOCUMENT_PARAM
        self.KO_ASSOCIATION_VIEW_PARAM = KO_ASSOCIATION_VIEW_PARAM

    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Подключение/запуск КОМПАС. Бросает KompasConnectionError."""
        if not PYWIN32_OK:
            raise KompasConnectionError(
                "Не установлен пакет pywin32. Выполните: pip install pywin32")

        # Инициализация COM для текущего потока (рабочий поток GUI).
        pythoncom.CoInitialize()

        # 1) Библиотеки типов. При отказе (например, в замороженном exe)
        #    продолжаем на динамическом диспатче с локальными константами.
        try:
            self.constants = gencache.EnsureModule(TYPELIB_CONSTANTS, 0, 1, 0).constants
        except Exception as exc:
            self.log("warning", f"Не загрузилась typelib констант ({exc}); "
                                "использую локальные значения констант")
        try:
            self.api5_module = gencache.EnsureModule(TYPELIB_API5, 0, 1, 0)
        except Exception as exc:
            self.log("warning", f"Не загрузилась typelib API5 ({exc}); "
                                "2D-структуры будут динамическими")
        try:
            self.api7_module = gencache.EnsureModule(TYPELIB_API7, 0, 1, 0)
        except Exception as exc:
            self.log("warning", f"Не загрузилась typelib API7 ({exc}); "
                                "API7 будет динамическим")

        # Устаревший кеш (после обновления КОМПАС): в обёртках API7 нет
        # нужных интерфейсов. Сбрасываем кеш и генерируем обёртки заново.
        if self.api7_module is not None and not all(
                hasattr(self.api7_module, name)
                for name in ("IKompasDocument3D", "IAssemblyDocument")):
            self.log("warning", "Кеш COM-обёрток устарел — пересоздаю")
            clear_com_cache()
            try:
                self.api7_module = gencache.EnsureModule(TYPELIB_API7, 0, 1, 0)
                self.api5_module = gencache.EnsureModule(TYPELIB_API5, 0, 1, 0)
                self.log("info", "Кеш COM-обёрток пересоздан")
            except Exception as exc:
                self.api7_module = self.api5_module = None
                self.log("warning", f"Кеш не пересоздан ({exc}); работаю "
                                    "на динамическом диспатче")

        if self.constants is not None:
            self.KO_DOCUMENT_PARAM = int(getattr(
                self.constants, "ko_DocumentParam", KO_DOCUMENT_PARAM))
            self.KO_ASSOCIATION_VIEW_PARAM = int(getattr(
                self.constants, "ko_AssociationViewParam", KO_ASSOCIATION_VIEW_PARAM))

        # 2) API7: сначала подключаемся к работающему экземпляру.
        raw_app = None
        last_error = ""
        for progid in PROGIDS_API7:
            try:
                raw_app = GetActiveObject(progid)
                self.log("info", f"Подключено к работающему КОМПАС ({progid})")
                break
            except Exception as exc:
                last_error = str(exc)
        # Не нашли запущенный — создаём новый экземпляр.
        if raw_app is None:
            for progid in PROGIDS_API7:
                try:
                    raw_app = Dispatch(progid)
                    self.started_by_us = True
                    self.log("info", f"Запущен новый экземпляр КОМПАС ({progid})")
                    break
                except Exception as exc:
                    last_error = str(exc)
        if raw_app is None:
            raise KompasConnectionError(
                "КОМПАС-3D не найден (KOMPAS.Application.7/8). "
                f"Убедитесь, что КОМПАС v24 установлен. Последняя ошибка: {last_error}")

        # 3) Приводим к раннему интерфейсу IApplication (API7).
        try:
            self.application = self.api7_module.IApplication(
                raw_app._oleobj_.QueryInterface(
                    self.api7_module.IApplication.CLSID, pythoncom.IID_IDispatch))
        except Exception:
            # Fallback: динамический диспатч (методы те же, без ранних типов).
            self.application = raw_app
            self.log("warning", "IApplication: использую динамический диспатч")

        # 4) API5 (KompasObject) — тот же процесс, что и API7.
        try:
            raw5 = Dispatch(PROGID_API5)
        except Exception as exc:
            raise KompasConnectionError(
                f"Не удалось подключить API5 ({PROGID_API5}): {exc}")
        try:
            self.kompas_object = self.api5_module.KompasObject(
                raw5._oleobj_.QueryInterface(
                    self.api5_module.KompasObject.CLSID, pythoncom.IID_IDispatch))
        except Exception:
            # Как и для API7: без ранних обёрток — динамический диспатч.
            self.kompas_object = raw5
            self.log("warning", "KompasObject: использую динамический диспатч")

        # 5) Окно КОМПАС должно быть видно: пользователь работает с моделью
        #    на паузах. Свойство может игнорироваться — не критично.
        try:
            self.application.Visible = True
        except Exception:
            self.log("warning", "Не удалось сделать окно КОМПАС видимым")

    # ------------------------------------------------------------------

    def pump(self, seconds: float) -> None:
        """Прокачка сообщений COM + ожидание (КОМПАС «дышит», GUI не вешаем)."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
            time.sleep(0.2)
