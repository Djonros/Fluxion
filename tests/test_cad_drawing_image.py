"""«Чертёж из PDF/картинки → деталь» (cad/drawing_image.py): pypdf-текст, страницы-сканы
через stand-in распознавания Windows (rag/ocr), без KOMPAS-3D и без vision-модели."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pypdf")
PIL = pytest.importorskip("PIL")

from cad import drawing_image


def make_pdf(path: Path, text: str) -> Path:
    """A one-page PDF with *text* in Helvetica (ASCII), offsets computed for the xref."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def test_vector_pdf_is_read_by_the_text(tmp_path, monkeypatch):
    """A drawing exported to PDF keeps its sizes as text: pypdf reads them, no OCR asked."""
    from rag import ocr

    path = make_pdf(tmp_path / "plate.pdf", "PLATE L=200 W=100 TH=8")
    monkeypatch.setattr(ocr, "available", lambda lang="ru": False)  # no OCR: the text stays enough
    text = drawing_image.summarize(path)
    assert "[Чертёж plate.pdf]" in text and "[Страница 1]" in text
    assert "Числа на чертеже: 200, 100, 8." in text
    assert "Надписи: «PLATE L=200 W=100 TH=8»." in text


def test_pdf_scan_pages_go_through_the_ocr(tmp_path, monkeypatch):
    """A scanned page (no text, one picture) is recognised like the archive reads scans."""
    from PIL import Image

    from rag import ocr

    path = make_pdf(tmp_path / "scan.pdf", "x")  # pypdf must open it; page text is a stub
    picture = tmp_path / "p1.png"
    Image.new("L", (300, 200), 255).save(picture)
    monkeypatch.setattr(ocr, "available", lambda lang="ru": True)
    monkeypatch.setattr(ocr, "pdf_scan_pages", lambda p, folder: {1: picture})
    monkeypatch.setattr(ocr, "recognize", lambda images, lang="ru": {str(picture): "Пластина 200 x 100, Сталь 3"})
    text = drawing_image.summarize(path)
    assert "[Чертёж scan.pdf]" in text
    assert "«Пластина 200 x 100, Сталь 3»." in text  # merged with the page text stub «x»
    assert "Числа на чертеже: 200, 100, 3." in text


def test_picture_is_read_by_the_ocr(tmp_path, monkeypatch):
    from PIL import Image

    from rag import ocr

    path = tmp_path / "чертёж.png"
    Image.new("L", (640, 400), 255).save(path)
    monkeypatch.setattr(ocr, "available", lambda lang="ru": True)
    monkeypatch.setattr(ocr, "text_of_image", lambda p, lang="ru": "Фланец ⌀150\nОтверстия 6 шт")
    text = drawing_image.summarize(path)
    assert "[Чертёж чертёж.png]" in text
    assert "Картинка: 640 × 400 пикселей." in text
    assert "Надписи: «Фланец ⌀150», «Отверстия 6 шт»." in text
    assert "Числа на чертеже: 150, 6." in text


def test_vision_description_is_added_when_the_model_exists(tmp_path, monkeypatch):
    """When the PC has a vision model, its reading goes into the summary with the OCR text."""
    from PIL import Image

    from rag import ocr

    path = tmp_path / "drawing.png"
    Image.new("L", (200, 100), 255).save(path)
    monkeypatch.setattr(ocr, "available", lambda lang="ru": True)
    monkeypatch.setattr(ocr, "text_of_image", lambda p, lang="ru": "")
    text = drawing_image.summarize(path, describe=lambda p: "Пластина 300 на 150, толщина 10, 4 отверстия ⌀12")
    assert "Описание модели для фото:" in text
    assert "Пластина 300 на 150, толщина 10, 4 отверстия ⌀12" in text


def test_picture_without_ocr_or_vision_is_a_problem(tmp_path, monkeypatch):
    from PIL import Image

    from rag import ocr

    path = tmp_path / "photo.png"
    Image.new("L", (100, 100), 255).save(path)
    monkeypatch.setattr(ocr, "available", lambda lang="ru": False)
    blocks, problems = drawing_image.blocks([path])
    assert not blocks and len(problems) == 1 and "photo.png" in problems[0]
    assert "распознавания текста" in problems[0]


def test_broken_picture_is_a_problem_not_an_exception(tmp_path, monkeypatch):
    from rag import ocr

    path = tmp_path / "broken.png"
    path.write_bytes(b"not a picture")
    monkeypatch.setattr(ocr, "available", lambda lang="ru": True)
    _, problems = drawing_image.blocks([path])
    assert len(problems) == 1 and "broken.png" in problems[0]


def test_suffixes_cover_pdf_and_pictures():
    assert ".pdf" in drawing_image.SUFFIXES
    assert {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"} <= drawing_image.SUFFIXES


def test_conversation_takes_the_summary_like_a_dxf():
    """The summary goes into cad.text2cad.Conversation the same way the DXF one does."""
    PLATE = {"template": "plate", "name": "Пластина",
             "params": {"length": 200, "width": 100, "thickness": 8}, "material": "сталь 3"}

    class _Recorder:
        def generate(self, messages, gen=None):
            return json.dumps(PLATE, ensure_ascii=False)

    from cad.text2cad import Conversation

    conversation = Conversation(_Recorder(), drawing="[Чертёж plate.pdf]\nЧисла на чертеже: 200, 100, 8.")
    conversation.step("деталь по чертежу, толщина 8")
    assert conversation.description.startswith("[Чертёж plate.pdf]")


def test_worker_reads_pdf_and_dxf_together(tmp_path, monkeypatch):
    """CadWorker reads a picture in one turn (no QThread.start: a straight call)."""
    from PIL import Image

    from rag import ocr

    monkeypatch.setattr(ocr, "available", lambda lang="ru": True)
    monkeypatch.setattr(ocr, "text_of_image", lambda p, lang="ru": "Пластина 200 x 100")

    class _Recorder:
        def generate(self, messages, gen=None):
            return json.dumps({"template": "plate", "name": "Пластина",
                               "params": {"length": 200, "width": 100, "thickness": 8},
                               "material": "сталь 3"}, ensure_ascii=False)

    from cad.text2cad import Conversation

    picture = tmp_path / "scan.png"
    Image.new("L", (400, 200), 255).save(picture)
    conversation = Conversation(_Recorder())
    from desktop_browser.app import CadWorker

    worker = CadWorker(conversation, "деталь по чертежу", drawings=[picture])
    worker.run()
    assert conversation.drawing.startswith("[Чертёж scan.png]")
    assert "Пластина 200 x 100" in conversation.drawing
