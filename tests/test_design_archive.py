"""«Архив КД»: cards of KOMPAS files read without KOMPAS (rag/kompas_files.py) and the
full-text index of an archive (rag/archive.py) — on made-up files, no KOMPAS needed."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag import archive, kompas_files
from rag.archive import ArchiveIndex, Filters, match_query, parse_filters

FILE_INFO = "[FileInfo]\nAppName=КОМПАС-3D v24\nCreateData={date} 10:00:00\nModifyData=30.04.2026 10:18:03\n"


def _marking(base: str, number: str = "", doc: str = "") -> str:
    return (f'<property id="marking"><property id="base" value="{base}" type="string" />'
            f'<property id="embodimentNumber" value="{number}" type="string" />'
            f'<property id="documentNumber" value="{doc}" type="string" /></property>')


def _object(kind: str, marking: str, name: str, material: str = "", mass: str = "", position: str = "",
            count: str = "", file: str = "") -> str:
    parts = [f'<infObject id="x" type="{kind}">']
    if file:
        parts.append(f'<document><property id="fullFileName" value="{file}" /></document>')
    if count:
        parts.append(f'<ownerEmbodiment id="e"><property id="count" value="{count}" type="int" /></ownerEmbodiment>')
    parts.append(f'<property id="name" value="{name}" type="string" />')
    if marking:
        parts.append(_marking(marking))
    if material:
        parts.append(f'<property id="material"><property id="name" value="{material}" type="string" /></property>')
    if mass:
        parts.append(f'<property id="mass" value="{mass}" type="double" />')
    if position:
        parts.append(f'<property id="position" value="{position}" type="string" />')
    parts.append('<property id="stampAuthor" value="Петров" type="string" />' if kind == "embodiment" else "")
    parts.append("</infObject>")
    return "".join(parts)


def kompas_file(path: Path, objects: list[str], date: str = "12.12.2023") -> Path:
    """A KOMPAS file as KOMPAS 21+ writes it: a zip with UTF-16 parts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    xml = ('<?xml version="1.0"?><document><descriptions /><product id="p">' + "".join(objects)
           + "</product></document>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("FileInfo", FILE_INFO.format(date=date).encode("utf-16"))
        z.writestr("MetaProductInfo", xml.encode("utf-16"))
        z.writestr("Contents", b"KF\x00binary")
    return path


SHEET = "Лист$dБ-ПУ-О-10 ГОСТ 19903-2015;345-09Г2С ГОСТ 17066-94$"


def archive_tree(root: Path) -> Path:
    """Two products: 714094 (a side member drawing, the frame specification) and 792720
    (a part of 2019 from Ст3)."""
    kompas_file(root / "714094" / "Рабочка" / "714094.2801094 - Боковой лонжерон.cdw",
                [_object("embodiment", "714094.2801094", "Боковой лонжерон", SHEET, "39.105")])
    kompas_file(root / "714094" / "Рабочка" / "714094.2801010 СП - Рама.spw",
                [_object("embodiment", "714094.2801010", "Рама", "Сталь 10 ГОСТ 1050-2013", "2081.5"),
                 _object("group_obj", "714094.2801094", "Боковой лонжерон", SHEET, "39.105", "1", "2"),
                 _object("group_obj", "", "Болт М10-6gx60 ГОСТ 7796-70", "", "0.044", "3", "8")])
    kompas_file(root / "792720" / "Модель" / "792720.2801201 - Поперечина.m3d",
                [_object("embodiment", "792720.2801201", "Поперечина"),
                 _object("body", "", "Тело 1", "Лист$d8 ГОСТ 19903-2015;Ст3сп ГОСТ 16523-97$", "12.5")],
                date="05.03.2019")
    (root / "714094" / "Рабочка" / "Рама.spw.bak").write_bytes(b"old copy")  # skipped
    (root / "714094" / "notes.txt").write_text("not an archive file", encoding="utf-8")  # not read
    return root


def test_card_of_a_drawing_a_specification_and_a_part(tmp_path):
    root = archive_tree(tmp_path / "Архив")
    drawing = kompas_files.read(root / "714094" / "Рабочка" / "714094.2801094 - Боковой лонжерон.cdw")
    assert "Обозначение и наименование: 714094.2801094 Боковой лонжерон" in drawing.text
    assert "Материал: Лист Б-ПУ-О-10 ГОСТ 19903-2015 / 345-09Г2С ГОСТ 17066-94" in drawing.text
    assert "Масса: 39.105 кг" in drawing.text and "Разработал: Петров" in drawing.text
    assert drawing.created == "12.12.2023" and drawing.year == 2023
    spec = kompas_files.card(root / "714094" / "Рабочка" / "714094.2801010 СП - Рама.spw")
    assert spec.startswith("Спецификация: 714094.2801010 СП - Рама")
    assert "Материал:" not in spec  # the KOMPAS default of an assembly is no material
    assert "- поз. 1 — 714094.2801094 Боковой лонжерон — 2 шт. — Лист Б-ПУ-О-10" in spec
    assert "поз. 3 — Болт М10-6gx60 ГОСТ 7796-70 — 8 шт." in spec
    part = kompas_files.read(root / "792720" / "Модель" / "792720.2801201 - Поперечина.m3d")
    assert "Материал: Лист 8 ГОСТ 19903-2015 / Ст3сп ГОСТ 16523-97" in part.text  # from its body
    assert part.year == 2019


def test_not_a_kompas_file(tmp_path):
    bad = tmp_path / "old.cdw"
    bad.write_bytes(b"\xd0\xcf\x11\xe0 an old KOMPAS 5 file")
    with pytest.raises(kompas_files.KompasFileError):
        kompas_files.read(bad)


def test_filters_said_in_a_question():
    products = {"714094", "792720", "733361М"}
    f = parse_filters("материал поперечины в 714094 после 2019", products)
    assert f.products == ["714094"] and f.year_from == 2020 and f.year_to == 0
    assert parse_filters("что делали в 2016 году", products).year_from == 2016
    f = parse_filters("рамы 2016-2018 для 733361м", products)
    assert (f.year_from, f.year_to, f.products) == (2016, 2018, ["733361М"])
    assert parse_filters("до 2018", products).year_to == 2017
    q = match_query("из какого материала боковой лонжерон 714094.2801094", Filters(["714094"]))
    assert '"714094 2801094"' in q and "материал*" in q and "лонжерон*" in q and "какого" not in q


def test_update_reads_only_new_and_changed_and_drops_the_gone(tmp_path):
    root = archive_tree(tmp_path / "Архив")
    index = ArchiveIndex(tmp_path / "data", [str(root)], ocr=False)
    stats = index.update()
    assert (stats.files, stats.indexed, stats.unchanged) == (3, 3, 0) and not stats.failed
    assert index.products == {"714094", "792720"} and index.file_count == 3
    again = index.update()
    assert (again.indexed, again.unchanged) == (0, 3)
    part = root / "792720" / "Модель" / "792720.2801201 - Поперечина.m3d"
    kompas_file(part, [_object("embodiment", "792720.2801201", "Поперечина усиленная")], date="05.03.2019")
    os.utime(part, (part.stat().st_atime, part.stat().st_mtime + 10))
    (root / "714094" / "Рабочка" / "714094.2801010 СП - Рама.spw").unlink()
    third = index.update()
    assert (third.indexed, third.removed) == (1, 1)
    assert index.search("усиленная поперечина") and not index.search("болт М10")
    index.close()


def test_a_stopped_update_goes_on_from_where_it_stopped(tmp_path):
    root = archive_tree(tmp_path / "Архив")
    index = ArchiveIndex(tmp_path / "data", [str(root)], ocr=False)
    asked = []
    first = index.update(stop_requested=lambda: len(asked) >= 1, progress=lambda d, n, name: asked.append(name))
    assert first.stopped and first.indexed == 1
    second = index.update()
    assert second.indexed == 2 and second.unchanged == 1
    index.close()


def test_search_by_words_designations_and_filters(tmp_path):
    root = archive_tree(tmp_path / "Архив")
    index = ArchiveIndex(tmp_path / "data", [str(root)], ocr=False)
    index.update()
    hits = index.search("из какого материала боковой лонжерон")
    assert hits and Path(hits[0].file_path).name == "714094.2801094 - Боковой лонжерон.cdw"
    assert "09Г2С" in hits[0].content and hits[0].kind == "Чертёж" and hits[0].year == 2023
    assert {Path(h.file_path).suffix for h in index.search("714094.2801094")} == {".cdw", ".spw"}
    assert [Path(h.file_path).suffix for h in index.search("поперечина Ст3сп в 792720")] == [".m3d"]
    assert not index.search("поперечина 714094")  # filtered to another product
    assert {h.year for h in index.search("лист после 2020")} == {2023}  # not the part of 2019
    assert index.search("") == [] and index.search('"(') == []
    index.close()


def test_the_assistant_answers_from_the_archive_with_sources(tmp_path):
    from orchestrator.assistant import Assistant
    from rag.service import RAGService

    root = archive_tree(tmp_path / "Архив")
    index = ArchiveIndex(tmp_path / "data", [str(root)], ocr=False)
    index.update()
    seen = []
    backend = SimpleNamespace(stream=lambda messages, gen=None: seen.append(messages) or iter(["Ок"]))
    from core.config import Settings

    assistant = Assistant(backend=backend, settings=Settings(), rag_service=None, web_search=None)
    result, stream = assistant.ask_stream("материал бокового лонжерона", source=index)
    assert "".join(stream) == "Ок" and result.rag_used
    assert result.citations[0]["label"] == "714094.2801094 - Боковой лонжерон.cdw"
    context = "\n".join(m["content"] for m in seen[0])
    assert "[1] 714094.2801094 - Боковой лонжерон.cdw" in context and "09Г2С" in context
    assert RAGService.build_context  # the same context builder as the project index
    index.close()


def test_archive_command_and_pro(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    import licensing

    assert archive.PRO_FEATURE in licensing.PRO_FEATURES
    assert appmod.archive_command("/архив материал рамы") == "материал рамы"
    assert appmod.archive_command("/архив") == "" and appmod.archive_command("/архивы") is None
    QApplication.instance() or QApplication([])
    told = []
    monkeypatch.setattr(appmod.QMessageBox, "information", lambda parent, title, text: told.append(text))
    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: False)
    window = appmod.FluxionWindow(qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    monkeypatch.setattr(window, "_style_dir", lambda: str(tmp_path / "data"))
    window.prompt.setText("/архив материал рамы")
    window.send_message()
    assert told and "функция Pro" in told[0] and not window.generating
    monkeypatch.setattr(licensing, "feature_enabled", lambda feature: True)
    window.prompt.setText("/архив материал рамы")
    window.send_message()  # nothing indexed yet: the window says how to start
    assert "ещё не проиндексирован" in window.messages.toPlainText() and not window.generating
    window.close()


def test_scans_are_read_by_the_ocr(tmp_path, monkeypatch):
    """A scan saved as a picture and a scanned PDF page go through the OCR of Windows
    (rag/ocr.py); here the recognition is a stand-in."""
    from PIL import Image

    from rag import ocr

    root = tmp_path / "Архив"
    scan = root / "714094" / "Сканы" / "Акт испытаний.tif"
    scan.parent.mkdir(parents=True)
    Image.new("L", (6000, 300), 255).save(scan)  # wider than the OCR takes: scaled down
    sizes = []

    def fake_recognize(images, lang="ru"):
        sizes.extend(Image.open(p).size for p in images)
        return {str(p): "Акт испытаний тормозной системы 714094.3500001" for p in images}

    monkeypatch.setattr(ocr, "recognize", fake_recognize)
    index = ArchiveIndex(tmp_path / "data", [str(root)], ocr=True)
    stats = index.update()
    assert stats.indexed == 1 and max(sizes[0]) == ocr.MAX_SIDE
    hits = index.search("акт испытаний тормозной системы")
    assert hits and hits[0].kind == "Скан" and Path(hits[0].file_path).name == "Акт испытаний.tif"
    index.ocr = False  # without the OCR pictures are not part of the archive
    assert index.update().removed == 1
    index.close()


def test_pdf_scan_pages_get_the_recognised_text(tmp_path, monkeypatch):
    from rag import ocr

    pages = {2: tmp_path / "p2.png"}
    monkeypatch.setattr(ocr, "pdf_scan_pages", lambda path, folder: pages)
    monkeypatch.setattr(ocr, "recognize", lambda images, lang="ru": {str(pages[2]): "Протокол замеров"})
    text = ocr.text_of_pdf_scans(tmp_path / "a.pdf", "[Страница 1]\nТекст первой\n\n[Страница 3]\nТретья")
    assert text == "[Страница 1]\nТекст первой\n\n[Страница 2]\nПротокол замеров\n\n[Страница 3]\nТретья"


@pytest.mark.skipif(os.name != "nt", reason="the OCR of Windows")
def test_windows_ocr_reads_a_picture(tmp_path):
    from PIL import Image, ImageDraw, ImageFont

    from rag import ocr

    if not ocr.available("en"):
        pytest.skip("no OCR language in this Windows")
    picture = tmp_path / "scan.png"
    image = Image.new("L", (900, 200), 255)
    try:
        font = ImageFont.truetype("arial.ttf", 60)
    except OSError:
        pytest.skip("no Arial")
    ImageDraw.Draw(image).text((20, 60), "DRAWING 714094", fill=0, font=font)
    image.save(picture)
    assert "714094" in ocr.recognize([picture], "en")[str(picture)]


def test_archive_window_updates_the_index(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from desktop_browser.archive_dialog import ArchiveDialog
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    import time

    app = QApplication.instance() or QApplication([])
    root = archive_tree(tmp_path / "Архив")
    data = tmp_path / "data"
    ArchiveIndex(data, ocr=False).close()
    dialog = ArchiveDialog(data)
    assert "ещё не создан" in dialog.state.text()
    dialog.folders.addItem(str(root))
    dialog.ocr.setChecked(False)
    dialog.start_update()
    deadline = time.time() + 20
    while dialog.worker is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert "В индексе файлов: 3" in dialog.state.text() and "Прочитано файлов: 3" in dialog.state.text()
    dialog.reject()
    index = ArchiveIndex(data)
    assert index.roots == [str(root)] and not index.ocr
    index.close()


def test_copies_of_a_file_are_one_hit_and_years_are_not_words(tmp_path):
    root = archive_tree(tmp_path / "Архив")
    original = root / "714094" / "Рабочка" / "714094.2801094 - Боковой лонжерон.cdw"
    copy = root / "792720" / "Чужое" / "714094.2801094 - Боковой лонжерон(1).cdw"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(original.read_bytes())
    index = ArchiveIndex(tmp_path / "data", [str(root)], ocr=False)
    index.update()
    names = [Path(h.file_path).name for h in index.search("боковой лонжерон 09Г2С")]
    assert len([n for n in names if n.startswith("714094.2801094 - Боковой")]) == 1
    assert "2022" not in match_query("лист после 2022", parse_filters("лист после 2022", set()))
    index.close()


def test_dxf_drawing_texts_and_title_block(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    from rag import legacy_files

    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    msp.add_text("Полка верхняя", dxfattribs={"height": 5})
    msp.add_mtext(r"ПУ-О-16 ГОСТ 19903-74\P325-09Г2С ГОСТ 19281-89")  # \P: a new paragraph of MTEXT
    msp.add_text("1250")  # a number alone (a dimension) says nothing
    stamp = doc.blocks.new("STAMP")
    stamp.add_attdef("AUTHOR", text="")
    msp.add_blockref("STAMP", (0, 0)).add_attrib("AUTHOR", "Шаяхметов")
    path = tmp_path / "733630.2801025 - Полка верхняя.dxf"
    doc.saveas(path)
    text, year = legacy_files.dxf_text(path)
    assert text.startswith("Чертёж DXF: 733630.2801025 - Полка верхняя")
    assert "325-09Г2С ГОСТ 19281-89" in text and "Шаяхметов" in text and "1250" not in text
    assert year >= 2024  # the header keeps when the drawing was made


def test_old_word_and_excel_files_and_dwg_without_oda(tmp_path, monkeypatch):
    from rag import legacy_files

    doc = tmp_path / "714097.0000010 РЭ.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
                    + "Руководство по эксплуатации полуприцепа".encode("utf-16-le") + b"\x00\x00"
                    + "Root Entry".encode("utf-16-le") + b"\x00" * 8 + "Calibri Light".encode("cp1251"))
    text = legacy_files.old_office_text(doc)
    assert "Руководство по эксплуатации полуприцепа" in text
    assert "Root Entry" not in text and "Calibri" not in text
    not_ole = tmp_path / "new.xls"
    not_ole.write_bytes(b"PK zip")
    with pytest.raises(legacy_files.LegacyFileError):
        legacy_files.old_office_text(not_ole)
    import cad.acad

    monkeypatch.setattr(cad.acad, "oda_converter", lambda: None)
    dwg = tmp_path / "a.dwg"
    dwg.write_bytes(b"AC1027")
    with pytest.raises(legacy_files.LegacyFileError, match="ODA File Converter"):
        legacy_files.dwg_text(dwg)


def test_the_archive_reads_documents_of_all_kinds(tmp_path):
    root = archive_tree(tmp_path / "Архив")
    (root / "714097").mkdir()
    (root / "714097" / "РЭ.doc").write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 16
                                             + "Стояночная тормозная система".encode("utf-16-le"))
    index = ArchiveIndex(tmp_path / "data", [str(root)], ocr=False)
    assert index.update().indexed == 4
    hits = index.search("стояночная тормозная система")
    assert hits and hits[0].kind == "Документ Word" and hits[0].product == "714097"
    index.close()
