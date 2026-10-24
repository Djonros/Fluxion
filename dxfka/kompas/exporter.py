# -*- coding: utf-8 -*-
"""Экспорт одной детали КОМПАС в DXF."""

import os
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from dxfka.config import (
    AFTER_CREATE_DOC_S, AFTER_VIEW_S, DOC_TYPE_FRAGMENT,
    FLAT_PROJECTION_NAME, FRONT_PROJECTION_NAME, OPEN_RETRY_COUNT,
    OPEN_RETRY_WAIT_S, PLATE_PROJECTIONS, REBUILD_WAIT_S, SAVED_VIEW_NAME)
from dxfka.compat import dynamic_dispatch, pythoncom
from dxfka.kompas.connection import KompasConnection
from dxfka.kompas.walker import AssemblyWalker
from dxfka.models import CancelledByUser, PartInfo, SkippedByUser
from dxfka.utils import (
    com_count, com_item, com_items, dxf_extent_area, dxf_geometry_count,
    safe_str, sanitize_filename, strip_dxf_annotations,
    thickness_from_material)

# ======================================================================
# ЭКСПОРТ ОДНОЙ ДЕТАЛИ В DXF
# ======================================================================

class PartExporter:
    """
    Полный цикл обработки одной детали:

      листовая:   открыть -> ISheetMetalBody -> Straighten=True -> rebuild ->
                  SaveAs(временный .m3d) -> ПАУЗА (проверка развертки) ->
                  Straighten=False -> закрыть -> фрагмент с видом из
                  временного файла -> ksSaveToDXF. Fallback: проекция
                  «#Развертка» от исходного файла.

      нелистовая: открыть -> ПАУЗА (пользователь вращает модель, вид
                  «DXF_Export» подхватывается при наличии) -> SaveAs(врем.
                  .m3d) -> закрыть -> фрагмент с видом из временного файла
                  -> ksSaveToDXF. Fallback: проекция «#Спереди».

    Документ создаётся как ФРАГМЕНТ (type=2): без рамки, штампа и оформления —
    в DXF остаётся только геометрия. Масштаб вида 1:1.
    """

    def __init__(self, conn: KompasConnection,
                 log: Callable[[str, str], None], tmp_dir: str,
                 template_path: Optional[str] = None):
        self.conn = conn
        self.log = log
        self.tmp_dir = tmp_dir
        # Шаблон фрагмента (.frw): если задан и существует — фрагменты
        # создаются из него (без лишних надписей/оформления по умолчанию).
        self.template_path = template_path or None
        # Толщина последней листовой детали, прочитанная из модели
        # ("" — не удалось). Выставляется до первой паузы детали.
        self.detected_thickness = ""
        # Автоматический режим (ставит рабочий поток): плоская нелистовая
        # деталь с толщиной из материала экспортируется без паузы.
        self.auto_mode = False

    # ------------------------------------------------------------------
    # Вспомогательные операции с документами
    # ------------------------------------------------------------------

    def _open_or_reuse(self, path: str) -> Tuple[Any, bool]:
        """
        Открыть деталь; если она уже открыта пользователем — использовать
        существующий документ (не закрывать после).
        Возвращает (doc, opened_by_us).
        """
        app = self.conn.application
        target = os.path.normcase(os.path.abspath(path))
        # Поиск среди открытых документов по PathName.
        try:
            docs = app.Documents
            count = com_count(docs) or 0
            for i in range(1, count + 1):   # TODO_KOMPAS: база индексации Documents
                doc = com_item(docs, i)
                if doc is None:
                    continue
                if os.path.normcase(safe_str(doc, "PathName")) == target:
                    self.log("info", "Документ уже открыт — использую существующий")
                    return doc, False
        except Exception as exc:
            self.log("warning", f"Перебор открытых документов не удался: {exc}")

        # Открытие с повторами (КОМПАС может быть занят модальным окном).
        for attempt in range(1, OPEN_RETRY_COUNT + 1):
            try:
                doc = app.Documents.Open(path)
                if doc is not None:
                    time.sleep(1.0)
                    return doc, True
            except Exception as exc:
                self.log("warning", f"Открытие не удалось (попытка {attempt}): {exc}")
            self.conn.pump(OPEN_RETRY_WAIT_S)
        return None, False

    def _close_doc(self, doc: Any, opened_by_us: bool) -> None:
        """Закрыть документ, если открывали мы. Пользовательский не трогаем."""
        if doc is None:
            return
        if not opened_by_us:
            self.log("info", "Документ был открыт пользователем — не закрываю")
            return
        try:
            doc.Close(False)
        except Exception:
            try:
                doc.Close()
            except Exception as exc:
                self.log("warning", f"Не удалось закрыть документ: {exc}")
        time.sleep(0.5)

    def _try_activate_saved_view(self, doc: Any, doc3d: Any, top: Any) -> bool:
        """
        Попытка найти и активировать сохранённый пользователем вид
        с именем «DXF_Export» (чтобы чертёж взял именно эту ориентацию).
        TODO_KOMPAS: реальные имена свойств/методов активации вида в 3D
        не документированы — перебираем кандидатов, всё в try/except.
        """
        candidates = []
        for getter in (
            lambda: doc.DocumentFrames,
            lambda: doc3d.Views,
            lambda: getattr(top, "Views", None),
            lambda: getattr(top, "DocumentFrames", None),
        ):
            try:
                coll = getter()
            except Exception:
                coll = None
            if coll is not None:
                candidates.append(coll)

        for coll in candidates:
            items = com_items(coll)
            for item in items:
                name = safe_str(item, "Name").strip()
                if name.lower() != SAVED_VIEW_NAME.lower():
                    continue
                self.log("info", f"Найден сохранённый вид «{name}» — активирую")
                for method in ("Activate", "SetCurrent", "SetActive"):
                    try:
                        getattr(item, method)()
                        self.log("info", f"Активация вида: {method}() — ок")
                        return True
                    except Exception:
                        continue
                self.log("warning", "Вид найден, но активировать не удалось")
        self.log("info", f"Сохранённый вид «{SAVED_VIEW_NAME}» не найден — "
                         "использую текущую ориентацию модели")
        return False

    # ------------------------------------------------------------------
    # 2D-конвейер: фрагмент -> ассоциативный вид -> DXF
    # ------------------------------------------------------------------

    def _activate_current_document(self) -> None:
        """Активировать текущий документ КОМПАС (окно фрагмента — на передний
        план, чтобы пользователь мог править вид на паузе)."""
        app = getattr(self.conn, "application", None)
        if app is None:
            return
        try:
            doc7 = app.ActiveDocument
        except Exception:
            doc7 = None
        if doc7 is None:
            return
        try:
            doc7.Activate()
        except Exception:
            pass

    def _close_api7_doc(self, doc7: Any) -> None:
        """Закрыть документ API7 без сохранения (уборка при отказе шаблона)."""
        if doc7 is None:
            return
        try:
            doc7.Close(False)
        except Exception:
            pass
        time.sleep(0.3)

    def _build_fragment_view(self, source_m3d: str, projection_name: str,
                             dxf_path: str,
                             pause: Optional[Callable[[str, str], None]] = None
                             ) -> Tuple[int, bool, str]:
        """
        Создаёт фрагмент 1:1, вставляет ассоциативный вид из 3D-модели
        и сохраняет в DXF. Проверенная последовательность (batch_export_dxf_v3).
        Если передан pause — после создания вида выполнение останавливается:
        пользователь в КОМПАС поправляет ориентацию вида и удаляет лишние
        надписи/обозначения, затем жмёт «Продолжить» — и только после этого
        фрагмент сохраняется в DXF в отредактированном виде.
        Возвращает (ссылка вида, успех, сообщение).
        """
        i_document2d = None
        try:
            kompas = self.conn.kompas_object

            # 1) Объект 2D-документа API5. Приоритет — шаблон пользователя
            #    (.frw): фрагмент создаётся из шаблона (API7
            #    AddNewDocumentFromTemplateEx), 2D-интерфейс — через
            #    ActiveDocument2D. Не вышло — обычный пустой фрагмент.
            if self.template_path:
                if os.path.isfile(self.template_path):
                    try:
                        doc7 = self.conn.application.Documents.\
                            AddNewDocumentFromTemplateEx(self.template_path,
                                                         True)
                        time.sleep(AFTER_CREATE_DOC_S)
                        i_document2d = kompas.ActiveDocument2D()
                        if i_document2d is not None:
                            self.log("info", "Фрагмент создан из шаблона: "
                                     + os.path.basename(self.template_path))
                        else:
                            self.log("warning",
                                     "Шаблон не дал 2D-интерфейс — создаю "
                                     "обычный фрагмент")
                            self._close_api7_doc(doc7)
                    except Exception as exc:
                        i_document2d = None
                        self.log("warning",
                                 f"Шаблон фрагмента не применился: {exc}")
                else:
                    self.log("warning", "Файл шаблона не найден: "
                             f"{self.template_path} — обычный фрагмент")

            if i_document2d is None:
                i_document2d = kompas.Document2D()
                if i_document2d is None:
                    return 0, False, "Document2D() вернул None"

                # 2) Параметры документа: ФРАГМЕНТ (без рамки и штампа).
                doc_param = kompas.GetParamStruct(self.conn.KO_DOCUMENT_PARAM)
                doc_param.Init()
                doc_param.type = DOC_TYPE_FRAGMENT
                if not i_document2d.ksCreateDocument(doc_param):
                    return 0, False, \
                        "ksCreateDocument вернул 0 (фрагмент не создан)"
                time.sleep(AFTER_CREATE_DOC_S)

            # 3) Параметры ассоциативного вида с модели.
            raw_avp = kompas.GetParamStruct(self.conn.KO_ASSOCIATION_VIEW_PARAM)
            if self.conn.api5_module is not None:
                avp = self.conn.api5_module.ksAssociationViewParam(raw_avp)
            else:
                avp = raw_avp  # динамический fallback
            avp.Init()
            avp.disassembly = False          # без разборки сборки
            avp.fileName = source_m3d        # источник геометрии (3D-модель)
            avp.hiddenLinesShow = False      # невидимые линии не показывать
            avp.hiddenLinesStyle = 4
            avp.projBodies = True            # проектировать тела
            avp.projectionLink = False       # БЕЗ ассоциативной связи с моделью
            # Имя проекции: '' — текущая ориентация модели (для временных
            # файлов), '#Развертка' / '#Спереди' — стандартные проекции.
            avp.projectionName = projection_name
            avp.projSurfaces = True
            avp.projThreads = True
            avp.sameHatch = False
            avp.section = False              # не разрез
            avp.tangentEdgesShow = False
            avp.tangentEdgesStyle = 2
            avp.visibleLinesStyle = 1

            # 4) Параметры самого вида: масштаб 1:1, без подписи.
            raw_vp = avp.GetViewParam()
            if self.conn.api5_module is not None:
                vp = self.conn.api5_module.ksViewParam(raw_vp)
            else:
                vp = raw_vp
            vp.Init()
            vp.angle = 0
            vp.color = 0
            vp.name = ""       # пустое имя — без подписи вида
            vp.scale_ = 1      # МАСШТАБ 1:1
            vp.state = 0
            vp.x = 0           # точка вставки — начало координат фрагмента
            vp.y = 0

            # 5) Создание вида на листе. Ненулевая ссылка = успех (проверено).
            view_ref = i_document2d.ksCreateSheetArbitraryView(avp, 0)
            time.sleep(AFTER_VIEW_S)
            if not view_ref:
                return 0, False, (
                    "Вид не создан (ksCreateSheetArbitraryView = 0); "
                    f"источник: {os.path.basename(source_m3d)}, "
                    f"проекция: «{projection_name or '(текущая ориентация)'}»")

            # 6) ПАУЗА: пользователь правит вид в КОМПАС (поворот, удаление
            #    лишних надписей/обозначений), затем продолжает — DXF
            #    сохраняется в отредактированном виде.
            if pause is not None:
                self._activate_current_document()
                self.log("info", "Пауза: проверка вида во фрагменте")
                pause("Проверьте вид во фрагменте",
                      f"Чертёж-фрагмент для «{os.path.basename(source_m3d)}» "
                      "создан в КОМПАС.\n"
                      "Поверните вид правильной стороной (выделите вид и "
                      "поверните его),\n"
                      "при необходимости удалите лишние надписи и обозначения "
                      "(сгибы, оси и т.п.).\n"
                      "Затем нажмите «Продолжить» — фрагмент будет сохранён "
                      "в DXF в текущем виде.\n"
                      "«Пропустить деталь» — без сохранения DXF.")

            # 7) Перестройка чертежа: после правок пользователя (поворот
            #    вида, удаление надписей) объекты чертежа нужно
            #    переформировировать — иначе DXF сохранится «как было».
            try:
                if not i_document2d.ksRebuildDocument():
                    self.log("warning", "ksRebuildDocument вернул False")
            except Exception as exc:
                self.log("warning", f"ksRebuildDocument: {exc}")
            time.sleep(0.3)

            # 8) Экспорт в DXF.
            # TODO_KOMPAS: ksSaveToDXF не принимает параметров; версия/единицы
            # DXF определяются настройками КОМПАС (Параметры -> Совместимость ->
            # форматы обмена). Требуемый профиль: ASCII, мм — настройте в КОМПАС.
            save_result = i_document2d.ksSaveToDXF(dxf_path)
            if not (save_result and os.path.isfile(dxf_path)
                    and os.path.getsize(dxf_path) > 0):
                return view_ref, False, \
                    f"ksSaveToDXF вернул {save_result}, файл DXF не создан"

            # 9) В DXF остаётся только контур: подпись масштаба «(1:1)» и
            #    прочие надписи вида удаляются. Успех — по наличию
            #    геометрии, а не по размеру файла (пустой DXF ~600 КБ).
            removed = strip_dxf_annotations(dxf_path)
            if removed:
                self.log("info", f"Из DXF удалены надписи: {removed}")
            count = dxf_geometry_count(dxf_path)
            if not count:
                try:
                    os.remove(dxf_path)
                except OSError:
                    pass
                return view_ref, False, "В DXF нет контура детали (вид пуст)"
            return view_ref, True, f"DXF создан (элементов контура: {count})"

        except (CancelledByUser, SkippedByUser):
            raise  # «Отмена»/«Пропустить» на паузе — не ошибка, пробрасываем
        except Exception as exc:
            return 0, False, f"Ошибка при создании вида/DXF: {exc}"
        finally:
            # Фрагмент закрываем всегда, без сохранения.
            if i_document2d is not None:
                try:
                    i_document2d.ksCloseDocument()
                except Exception:
                    pass
                time.sleep(0.3)

    # ------------------------------------------------------------------
    # Локальные детали: выгрузка из сборки во временные .m3d
    # ------------------------------------------------------------------

    def export_local_parts(self, assembly_path: str,
                           local_parts: List[PartInfo]) -> Dict[str, str]:
        """
        Выгрузить выбранные локальные детали (is_local=True) из сборки
        во временные файлы .m3d. Возвращает {uid: temp_path} для успешно
        выгруженных; неудачи логируются (в списке их обработает цикл
        экспорта как ERROR). Сборка открывается один раз на группу.
        """
        resolved: Dict[str, str] = {}
        if not local_parts:
            return resolved
        doc, opened_by_us = self._open_or_reuse(assembly_path)
        if doc is None:
            self.log("error",
                     f"Не удалось открыть сборку для локальных деталей: "
                     f"{assembly_path}")
            return resolved
        try:
            walker = AssemblyWalker(self.conn, self.log)
            top = walker.get_top_part(doc)
            if top is None:
                self.log("error", "Не удалось получить TopPart сборки "
                                  "(локальные детали)")
                return resolved
            base_dir = os.path.dirname(os.path.abspath(assembly_path))
            pairs = walker.walk_top(top, base_dir,
                                    os.path.abspath(assembly_path))
            by_uid = {info.uid: obj for info, obj in pairs}
            for part in local_parts:
                obj = by_uid.get(part.uid)
                if obj is None:
                    self.log("error",
                             f"Локальная деталь не найдена при повторном "
                             f"обходе (порядок изменился?): {part.name}")
                    continue
                temp_m3d = os.path.join(
                    self.tmp_dir,
                    f"local_{part.local_seq:03d}_"
                    + sanitize_filename(part.name or "деталь") + ".m3d")
                if self._save_local_to_m3d(obj, temp_m3d):
                    resolved[part.uid] = temp_m3d
                    self.log("info",
                             f"Локальная деталь выгружена: {part.name} -> "
                             f"{os.path.basename(temp_m3d)}")
        finally:
            self._close_doc(doc, opened_by_us)
        return resolved

    def _save_local_to_m3d(self, obj: Any, temp_path: str) -> bool:
        """
        Сохранить локальную деталь (объект IPart7 из открытой сборки)
        отдельным файлом .m3d. Метод сохранения компонента перебираем
        кандидатами — точный API не подтверждён (TODO_KOMPAS: при первой
        проверке смотреть строки лога ниже).
        """
        if os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        candidates = (
            ("SaveAs", lambda: obj.SaveAs(temp_path)),
            ("SaveComponentAs", lambda: obj.SaveComponentAs(temp_path)),
            ("SaveToFile", lambda: obj.SaveToFile(temp_path)),
        )
        for label, call in candidates:
            try:
                call()
            except Exception as exc:
                self.log("warning",
                         f"Сохранение локальной детали ({label}): {exc}")
                continue
            time.sleep(0.5)
            if os.path.isfile(temp_path) and os.path.getsize(temp_path) > 0:
                return True
            self.log("warning",
                     f"{label} отработал без исключения, файл не создан")
        return False

    # ------------------------------------------------------------------
    # Главный метод: экспорт одной детали
    # ------------------------------------------------------------------

    def _qi(self, obj: Any, iface_name: str) -> Any:
        """QueryInterface к интерфейсу API7; бросает, если не поддерживается."""
        iface = getattr(self.conn.api7_module, iface_name)
        return iface(obj._oleobj_.QueryInterface(iface.CLSID,
                                                 pythoncom.IID_IDispatch))

    def _find_sheet_body(self, top: Any) -> Any:
        """Первое листовое тело детали (ISheetMetalBody) или None."""
        if self.conn.api7_module is None:
            self.log("warning", "Нет обёрток API7 — листовое тело не ищу")
            return None
        try:
            bodies = self._qi(top, "ISheetMetalContainer").SheetMetalBodies
            if bodies is None or not com_count(bodies):
                return None
            try:
                first = bodies.Item(0)      # проверено на v24
            except Exception:
                first = com_item(bodies, 0)
            return self._qi(first, "ISheetMetalBody")
        except Exception as exc:
            self.log("info", f"Листовое тело не найдено: {exc}")
            return None

    def _read_sheet_thickness(self, sheet_body: Any) -> str:
        """
        Толщина листового тела, мм, строкой («3», «1.5»); "" — не удалось.
        ISheetMetalBody.Thickness проверено на КОМПАС v24.
        """
        try:
            value = float(sheet_body.Thickness)
        except Exception as exc:
            self.log("warning", f"Толщину из модели прочитать не удалось: {exc}")
            return ""
        if value <= 0:
            self.log("warning", f"Толщина из модели некорректна: {value}")
            return ""
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        self.log("info", f"Толщина из модели: {text} мм")
        return text

    def _export_largest_projection(self, source: str, stem: str,
                                   dxf_path: str) -> Tuple[bool, str]:
        """
        Экспорт стандартных проекций во временные DXF и выбор той, где
        габарит контура наибольший (для пластины — вид на лицевую грань).
        """
        best_path, best_area, best_proj = "", 0.0, ""
        for index, projection in enumerate(PLATE_PROJECTIONS):
            tmp = os.path.join(self.tmp_dir,
                               f"{sanitize_filename(stem)}_proj{index}.dxf")
            if os.path.isfile(tmp):
                os.remove(tmp)
            _, ok, message = self._build_fragment_view(source, projection, tmp)
            area = dxf_extent_area(tmp) if ok else 0.0
            self.log("info", f"Проекция «{projection}»: "
                     + (f"габарит {area:.0f} мм²" if ok else message))
            if area > best_area:
                best_path, best_area, best_proj = tmp, area, projection
        if not best_path:
            return False, "Ни одна стандартная проекция не дала контур"
        shutil.copyfile(best_path, dxf_path)
        self.log("info", f"Выбрана проекция «{best_proj}»")
        return True, dxf_path

    def _fresh_dxf_path(self, dxf_path_factory: Callable[[], str]) -> str:
        """Путь DXF от фабрики рабочего потока + удаление старого файла."""
        dxf_path = dxf_path_factory()
        if os.path.isfile(dxf_path):
            try:
                os.remove(dxf_path)
            except OSError as exc:
                self.log("warning",
                         f"Не удалось удалить старый DXF: {exc}")
        return dxf_path

    def export_part(self, part: PartInfo,
                    dxf_path_factory: Callable[[], str],
                    pause: Callable[[str, str], None]) -> Tuple[str, str]:
        """
        Обработка одной детали. pause(title, instruction) — блокирующий
        вызов, бросает CancelledByUser/SkippedByUser. dxf_path_factory()
        вызывается непосредственно перед сохранением: путь (подпапка
        толщины) становится известен после первой паузы детали.
        Возвращает ("OK" | "ERROR", сообщение).
        """
        self.log("info", "=" * 70)
        self.log("info", f"Деталь: {os.path.basename(part.file_path)}")
        self.detected_thickness = ""

        state = {"ok": False}                 # для finally-уборки
        doc = None
        opened_by_us = False
        sheet_body = None
        top = None
        straighten_set = False
        temp_m3d: Optional[str] = None

        try:
            # --- A. Открытие детали и определение «листовая?» ---
            doc, opened_by_us = self._open_or_reuse(part.file_path)
            if doc is None:
                return "ERROR", "Не удалось открыть файл детали в КОМПАС"

            doc3d = None
            if self.conn.api7_module is not None:
                try:
                    doc3d = self.conn.api7_module.IKompasDocument3D(doc)
                except Exception as exc:
                    self.log("warning", f"IKompasDocument3D: {exc}")
            if doc3d is None:
                doc3d = dynamic_dispatch(doc)
            top = doc3d.TopPart
            if top is None:
                return "ERROR", "Не удалось получить TopPart детали"

            # Листовое тело: TopPart -> ISheetMetalContainer ->
            # SheetMetalBodies[0] -> ISheetMetalBody (настоящий
            # QueryInterface). Обёртка ISheetMetalBody(top) без QI «успешна»
            # всегда, а её свойства попадают в чужие свойства IPart7
            # (Thickness -> FileName, Straighten -> Standard) — проверено
            # на КОМПАС v24.
            sheet_body = self._find_sheet_body(top)
            part.is_sheet = sheet_body is not None
            self.log("info", "Тип детали: "
                     + ("листовая (ISheetMetalBody)" if part.is_sheet
                        else "нелистовая"))

            stem = os.path.splitext(os.path.basename(part.file_path))[0]

            # Толщина: листовое тело, иначе материал «Лист$d8 …»
            # (так у пластин, сделанных выдавливанием эскиза).
            material = part.material or safe_str(top, "Material")
            material_thk = thickness_from_material(material)
            body_thk = (self._read_sheet_thickness(sheet_body)
                        if sheet_body is not None else "")
            if body_thk and material_thk and body_thk != material_thk:
                self.log("warning",
                         f"Толщина в модели {body_thk} мм, в материале "
                         f"{material_thk} мм — беру из модели")
            elif material_thk and not body_thk:
                self.log("info", f"Толщина из материала «{material}»: "
                                 f"{material_thk} мм")
            self.detected_thickness = body_thk or material_thk

            if sheet_body is not None:
                # --- B1. ЛИСТОВАЯ: развертка через выпрямление тела ---
                temp_m3d = os.path.join(
                    self.tmp_dir, sanitize_filename(stem) + "_unfold.m3d")

                # Выпрямляем тело листовой детали и перестраиваем модель.
                sheet_body.Straighten = True
                straighten_set = True
                top.RebuildModel(True)
                doc3d.RebuildDocument()
                time.sleep(REBUILD_WAIT_S)

                # Сохраняем развёрнутое состояние во временный файл ДО паузы —
                # фиксируем детерминированное состояние для вида.
                if os.path.isfile(temp_m3d):
                    os.remove(temp_m3d)
                doc.SaveAs(temp_m3d)
                if not (os.path.isfile(temp_m3d)
                        and os.path.getsize(temp_m3d) > 0):
                    return "ERROR", \
                        "Не удалось сохранить развёрнутую модель во временный файл"

                # ПАУЗА: пользователь проверяет развертку.
                pause("Развертка построена — проверьте",
                      f"Развертка для детали «{stem}» построена.\n"
                      "Проверьте её в окне КОМПАС; при необходимости "
                      "откорректируйте ориентацию модели.\n"
                      "Нажмите «Продолжить» для создания фрагмента и экспорта "
                      "в DXF,\nлибо «Пропустить деталь».")

                # Возвращаем детали исходную (гнутую) форму и закрываем.
                try:
                    sheet_body.Straighten = False
                    straighten_set = False
                    top.RebuildModel(True)
                except Exception as exc:
                    self.log("warning", f"Straighten=False: {exc}")
                self._close_doc(doc, opened_by_us)
                doc = None

                # Фрагмент из временной (развёрнутой) модели, текущая
                # ориентация (пустая projectionName) — проверено test_unfold_v2.
                dxf_path = self._fresh_dxf_path(dxf_path_factory)
                _, ok, message = self._build_fragment_view(temp_m3d, "",
                                                           dxf_path, pause)
                if not ok:
                    # Fallback: проекция «#Развертка» от исходного файла
                    # (работает, если в модели построена плоская развертка).
                    self.log("warning",
                             "Вид из временной развертки не создан — пробую "
                             f"проекцию «{FLAT_PROJECTION_NAME}» от исходного файла")
                    _, ok, message = self._build_fragment_view(
                        part.file_path, FLAT_PROJECTION_NAME, dxf_path, pause)
                if not ok:
                    # Вид вместо развёртки не подставляем: у гнутой детали
                    # он дал бы неверную заготовку.
                    message = f"Развертка не построена: {message}"
                state["ok"] = ok
                return ("OK" if ok else "ERROR"), message

            # --- B2. НЕЛИСТОВАЯ: ориентацию задаёт пользователь ---
            # ПАУЗА: пользователь вращает модель / сохраняет вид «DXF_Export».
            pause("Поверните модель",
                  f"Поверните модель детали «{stem}» в КОМПАС нужной стороной.\n"
                  f"Рекомендуется сохранить текущий вид с именем "
                  f"«{SAVED_VIEW_NAME}» (команда «Сохранить вид»),\n"
                  "чтобы программа использовала именно эту ориентацию.\n"
                  "Затем нажмите «Продолжить».")

            # Подхватываем сохранённый вид, если пользователь его создал.
            saved_view = self._try_activate_saved_view(doc, doc3d, top)

            if self.auto_mode and self.detected_thickness and not saved_view:
                # Авто: пластина без паузы — лицевая сторона определяется
                # как проекция с наибольшим габаритом контура.
                self._close_doc(doc, opened_by_us)
                doc = None
                dxf_path = self._fresh_dxf_path(dxf_path_factory)
                ok, message = self._export_largest_projection(
                    part.file_path, stem, dxf_path)
                state["ok"] = ok
                return ("OK" if ok else "ERROR"), message

            # Фиксируем текущую ориентацию во временном файле; исходник не трогаем.
            # TODO_KOMPAS: механизм (SaveAs во врем. файл + пустая projectionName)
            # проверен для развертки; для произвольного поворота — fallback «#Спереди».
            temp_m3d = os.path.join(
                self.tmp_dir, sanitize_filename(stem) + "_view.m3d")
            if os.path.isfile(temp_m3d):
                os.remove(temp_m3d)
            doc.SaveAs(temp_m3d)
            if not (os.path.isfile(temp_m3d) and os.path.getsize(temp_m3d) > 0):
                return "ERROR", \
                    "Не удалось сохранить модель с выбранной ориентацией"
            self._close_doc(doc, opened_by_us)
            doc = None

            dxf_path = self._fresh_dxf_path(dxf_path_factory)
            _, ok, message = self._build_fragment_view(temp_m3d, "", dxf_path)
            if not ok:
                self.log("warning",
                         "Вид из временной модели не создан — fallback на "
                         f"проекцию «{FRONT_PROJECTION_NAME}»")
                _, ok, message = self._build_fragment_view(
                    part.file_path, FRONT_PROJECTION_NAME, dxf_path, pause)
            state["ok"] = ok
            return ("OK" if ok else "ERROR"), message

        finally:
            # Уборка при любом исходе (в т.ч. отмена/пропуск на паузе):
            # вернуть детали гнутую форму, закрыть документы, удалить temp.
            if doc is not None:
                if straighten_set and sheet_body is not None:
                    try:
                        sheet_body.Straighten = False
                        top.RebuildModel(True)
                    except Exception:
                        pass
                self._close_doc(doc, opened_by_us)
            if temp_m3d and os.path.isfile(temp_m3d):
                if state["ok"]:
                    try:
                        os.remove(temp_m3d)
                    except OSError:
                        pass
                else:
                    self.log("warning",
                             f"Временный файл сохранён для диагностики: {temp_m3d}")
