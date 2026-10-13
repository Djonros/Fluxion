"""Project index reads office documents: PDF, Word, Excel, PowerPoint."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from rag.chunker import TreeSitterChunker
from rag.documents import DocumentError, extract_text, is_document
from rag.indexer import SUPPORTED_EXTENSIONS, Indexer

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _zip(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return path


def make_docx(path: Path) -> Path:
    return _zip(path, {"word/document.xml": (
        f'<w:document {W}><w:body>'
        '<w:p><w:r><w:t>Договор поставки</w:t></w:r><w:r><w:t xml:space="preserve"> № 15</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Срок</w:t><w:tab/><w:t>30 дней</w:t></w:r></w:p>'
        '<w:p/>'
        '</w:body></w:document>'
    )})


def make_xlsx(path: Path) -> Path:
    return _zip(path, {
        "xl/workbook.xml": f'<workbook {S} {R}><sheets>'
                           '<sheet name="Продажи" sheetId="1" r:id="rId1"/>'
                           '<sheet name="Итог" sheetId="2" r:id="rId2"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>'
        ),
        "xl/sharedStrings.xml": f'<sst {S}><si><t>Товар</t></si><si><t>Болт</t></si></sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData>'
                                    '<row><c t="s"><v>0</v></c><c><v>12.5</v></c></row>'
                                    '<row><c t="s"><v>1</v></c><c t="b"><v>1</v></c></row>'
                                    '</sheetData></worksheet>',
        "xl/worksheets/sheet2.xml": f'<worksheet {S}><sheetData>'
                                    '<row><c t="inlineStr"><is><t>Всего</t></is></c></row>'
                                    '</sheetData></worksheet>',
    })


def make_pptx(path: Path) -> Path:
    slide = (f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" {A}>'
             '<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>')
    return _zip(path, {
        "ppt/slides/slide2.xml": slide.replace("{text}", "Итоги"),
        "ppt/slides/slide10.xml": slide.replace("{text}", "Вопросы"),
        "ppt/slides/slide1.xml": slide.replace("{text}", "План на квартал"),
    })


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


def test_docx(tmp_path):
    text = extract_text(make_docx(tmp_path / "a.docx"))
    assert text == "Договор поставки № 15\nСрок\t30 дней"


def test_xlsx_sheets_named_and_values_resolved(tmp_path):
    text = extract_text(make_xlsx(tmp_path / "b.xlsx"))
    assert "[Лист Продажи]\nТовар\t12.5\nБолт\tИСТИНА" in text
    assert "[Лист Итог]\nВсего" in text


def test_pptx_slides_in_order(tmp_path):
    text = extract_text(make_pptx(tmp_path / "c.pptx"))
    assert text.index("План на квартал") < text.index("Итоги") < text.index("Вопросы")
    assert "[Слайд 3]\nВопросы" in text


def test_pdf(tmp_path):
    pytest.importorskip("pypdf")
    text = extract_text(make_pdf(tmp_path / "d.pdf", "Invoice total 42 EUR"))
    assert "Invoice total 42 EUR" in text and text.startswith("[Страница 1]")


def test_damaged_document_is_reported(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(DocumentError, match="bad.docx"):
        extract_text(bad)


def test_too_large_document_is_skipped(tmp_path, monkeypatch):
    import rag.documents as documents

    monkeypatch.setattr(documents, "MAX_FILE_BYTES", 10)
    with pytest.raises(DocumentError, match="пропущен"):
        extract_text(make_docx(tmp_path / "big.docx"))


def test_indexer_collects_documents(tmp_path):
    for name in ("a.pdf", "b.docx", "c.xlsx", "d.pptx", "e.csv", "f.exe"):
        (tmp_path / name).write_bytes(b"x")
    assert {".pdf", ".docx", ".xlsx", ".pptx", ".csv"} <= SUPPORTED_EXTENSIONS
    found = {p.name for p in Indexer(None, None, None)._collect_files(tmp_path)}
    assert found == {"a.pdf", "b.docx", "c.xlsx", "d.pptx", "e.csv"}
    assert is_document("X.DOCX") and not is_document("e.csv")


def test_chunker_reads_documents(tmp_path):
    chunks = TreeSitterChunker(max_chars=40, overlap=5).chunk_file(make_xlsx(tmp_path / "b.xlsx"))
    assert chunks and all(c.language == "document" for c in chunks)
    assert "Болт" in "".join(c.content for c in chunks)
    assert chunks[0].start_line == 1 and chunks[-1].start_line > 1


def test_text_chunk_lines_follow_the_text():
    source = "".join(f"line {i}\n" for i in range(1, 200))
    chunks = TreeSitterChunker(max_chars=100, overlap=20)._chunk_text(source, "f.txt")
    for chunk in chunks:
        first = chunk.content.split("\n", 1)[0]
        assert first in source.splitlines()[chunk.start_line - 1]
