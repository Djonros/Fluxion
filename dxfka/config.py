# -*- coding: utf-8 -*-
"""Константы приложения, лицензирования и КОМПАС API."""

import os
import sys
import tempfile

# ======================================================================
# КОНСТАНТЫ: приложение, брендинг
# ======================================================================

APP_VERSION = "2.0.2"
APP_TITLE = "DXFka — Экспорт DXF из КОМПАС-3D"
VENDOR = "Djonros"
SUPPORT_EMAIL = "djonros@gmail.com"
LICENSE_CONTACT_HINT = ("В Fluxion «Пакет на лазер» — функция Pro: «Лицензия…» в меню «Помощь».")
TRIAL_EXHAUSTED_MSG = ("Лимит пробной версии исчерпан.\n" + LICENSE_CONTACT_HINT)
TRIAL_CORRUPTED_MSG = ("Состояние пробной версии повреждено "
                       "(обнаружена попытка сброса счётчика).\nЭкспорт заблокирован. "
                       + LICENSE_CONTACT_HINT)

# ======================================================================
# КОНСТАНТЫ: лицензирование / пробная версия
# ======================================================================

TRIAL_EXPORT_LIMIT = 10                     # сколько успешных экспортов даёт пробная версия
PRODUCT_ID = "kompas_dxf_exporter"          # идентификатор продукта в license.key
# in Fluxion: its own folder, apart from a standalone DXFka on the same computer
STATE_DIR = os.path.join(os.environ.get("APPDATA", tempfile.gettempdir()),
                         "Fluxion", "dxfka")            # %APPDATA%\Fluxion\dxfka
STATE_FILE = "state.json"                   # счётчик триала (файл)
SETTINGS_FILE = "settings.json"             # настройки GUI (шаблон, папка вывода)
LICENSE_FILE = "license.key"                # файл лицензии
REG_KEY = r"Software\KompasDxfExporter"     # зеркало счётчика в реестре (HKCU)

# In Fluxion the licence of the "Пакет на лазер" is the Pro licence of Fluxion:
# the secret and the trial counter of DXFka are not part of it.
DEV_MODE = False

# ======================================================================
# КОНСТАНТЫ: КОМПАС API и экспорт
# ======================================================================

# ProgID API7 в порядке приоритета (требование: сначала .7, затем .8).
PROGIDS_API7 = ["KOMPAS.Application.7", "KOMPAS.Application.8"]
PROGID_API5 = "Kompas.Application.5"        # API5 (KompasObject) — 2D/чертежи

# GUID библиотек типов КОМПАС (проверено на v24):
TYPELIB_CONSTANTS = "{75C9F5D0-B5B8-4526-8681-9903C567D2ED}"  # константы ks*
TYPELIB_API5 = "{0422828C-F174-495E-AC5D-D31014DBBE87}"       # API5 (KompasObject)
TYPELIB_API7 = "{69AC2981-37C0-4379-84FD-5DD2F3C0A520}"       # API7 (IApplication)

# Локальные значения констант — используются, если загрузка typelib не удалась
# (актуально для замороженного exe, где gencache может не работать).
KO_DOCUMENT_PARAM = 35                       # ko_DocumentParam
KO_ASSOCIATION_VIEW_PARAM = 122              # ko_AssociationViewParam

DOC_TYPE_FRAGMENT = 2                        # ksDocumentFragment: фрагмент БЕЗ рамки и штампа
DOC_TYPE_DRAWING = 1                         # ksDocumentDrawing (не используется, для справки)

FLAT_PROJECTION_NAME = "#Развертка"          # имя проекции-развертки (проверено работает)
FRONT_PROJECTION_NAME = "#Спереди"           # стандартная проекция (fallback для нелистовых)
# Проекции для плоской нелистовой детали в авто-режиме: берётся та,
# где контур занимает наибольшую площадь (лицевая сторона пластины).
PLATE_PROJECTIONS = ("#Спереди", "#Сверху", "#Слева")
SAVED_VIEW_NAME = "DXF_Export"               # имя сохранённого пользователем вида в 3D

# Пути, содержащие эти маркеры, считаем стандартными/библиотечными изделиями
# (Ascon/Program Files) и пропускаем при обходе сборки.
SKIP_PATH_MARKERS = ("program files", "ascon")

# Тайминги и повторы (КОМПАС иногда «задумывается» на больших моделях):
OPEN_RETRY_COUNT = 20          # попыток открыть документ
OPEN_RETRY_WAIT_S = 3.0        # пауза между попытками, сек (итого до ~60 с)
REBUILD_WAIT_S = 3.0           # ожидание перестройки модели после Straighten
AFTER_CREATE_DOC_S = 0.5       # пауза после ksCreateDocument
AFTER_VIEW_S = 1.0             # пауза после создания вида перед сохранением DXF
MAX_ASSEMBLY_DEPTH = 10        # защита от циклов в дереве подсборок
MAX_NAME_LEN = 150             # максимальная длина имени файла DXF (без .dxf)

# Каталог приложения: для exe — папка с exe, иначе корень репозитория
# (на уровень выше пакета dxfka).
# in Fluxion: the log goes with the other logs, never next to the exe
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "Fluxion")
LOG_FILE = os.path.join(APP_DIR, "logs", "dxfka.log")
DEFAULT_OUT_SUBDIR = "DXF_Out"

STARTUP_INSTRUCTIONS = (
    "Порядок работы:\n"
    "  1. Укажите файл сборки (.a3d) и папку для DXF.\n"
    "  2. Нажмите «Загрузить детали» — программа проанализирует сборку:\n"
    "     будут найдены все детали, включая исполнения и локальные (встроенные\n"
    "     в сборку, без своего файла). Не хватает — «Добавить файлы вручную».\n"
    "  3. Выберите режим в меню «Обработка»: «Автоматически» или «Вручную».\n"
    "  4. Отметьте нужные детали (флажок в первой колонке) и нажмите\n"
    "     «Начать экспорт».\n"
    "  5. Вручную — на ПЕРВОЙ паузе каждой детали определите вид для резки\n"
    "     (листовая — проверьте развертку; нелистовая — поверните модель,\n"
    "     при желании сохраните вид «DXF_Export»), затем укажите ТОЛЩИНУ\n"
    "     (мм) — DXF сохранится в подпапку с этим номером (создаётся сама).\n"
    "     Автоматически — толщина из листового тела или из материала\n"
    "     («Лист 8 …»), паузы пропускаются; остановка — только на деталях\n"
    "     без толщины.\n"
    "  6. Результат: DXF 1:1 в мм, только контур (без рамки и надписей).\n"
    f"  Поддержка и лицензии: {VENDOR} — {SUPPORT_EMAIL}"
)
