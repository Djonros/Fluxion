"""Answers from documents say where they come from: file and page (sheet, slide)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLUXION_DISABLE_WEBENGINE", "1")

import time
from types import SimpleNamespace

import pytest

from rag.chunker import TreeSitterChunker, place_label
from rag.retriever import RetrievalResult
from rag.service import RAGService, citation_label


def _pdf_text(pages: int, size: int = 1500) -> str:
    return "\n\n".join(f"[Страница {n}]\n" + chr(ord("а") + n) * size for n in range(1, pages + 1))


def test_document_chunks_know_their_pages():
    chunks = TreeSitterChunker()._chunk_text(_pdf_text(3), "договор.pdf", language="document")
    labels = [c.name for c in chunks]
    assert labels[0] == "стр. 1"
    assert "стр. 1–2" in labels or "стр. 2" in labels
    assert labels[-1].startswith("стр. ") and labels[-1].endswith("3")


def test_sheets_and_slides():
    sheet = TreeSitterChunker()._chunk_text("[Лист Смета]\nработа,цена\nсварка,100", "смета.xlsx", language="document")
    assert sheet[0].name == "лист Смета"
    marks = [(0, "Слайд", "4"), (50, "Слайд", "5")]
    assert place_label(marks, 10, 80) == "слайд 4–5"
    assert place_label(marks, 60, 80) == "слайд 5"
    assert place_label([], 0, 10) == ""


def test_code_is_not_labelled():
    chunks = TreeSitterChunker()._chunk_text("x = 1\n[Страница 2]\n", "a.txt", language="text")
    assert chunks[0].name == ""


def _result(path, name="", content="текст"):
    return RetrievalResult(content, path, 1, 2, "text", name, 0.9)


def test_context_asks_the_model_to_cite():
    ctx = RAGService.build_context([_result(r"C:\docs\договор.pdf", "стр. 3"), _result("app.py", "main")])
    assert "[1] договор.pdf, стр. 3" in ctx
    assert "app.py::main (L1-2)" in ctx  # code keeps its file and lines
    assert "square brackets" in ctx and "page" in ctx
    code_only = RAGService.build_context([_result("app.py", "main")])
    assert "Here are relevant code snippets" in code_only


def test_citation_label():
    assert citation_label(_result(r"C:\d\Смета.XLSX", "лист Итог")) == "Смета.XLSX, лист Итог"
    assert citation_label(_result("main.py", "f")) == ""


def test_assistant_keeps_citations():
    from orchestrator.assistant import AskResult, Assistant

    class Rag:
        def search(self, query, top_k=5):
            return [_result(r"C:\d\договор.pdf", "стр. 3"), _result("main.py", "f")]

    assistant = Assistant.__new__(Assistant)
    assistant.rag_service = Rag()
    result = AskResult(strategy=None, signals=None)
    assistant._do_rag("срок оплаты", result)
    assert result.citations == [{"n": 1, "file": r"C:\d\договор.pdf", "label": "договор.pdf, стр. 3"}]


def test_window_shows_sources_under_the_answer(tmp_path, monkeypatch):
    try:
        from PySide6.QtCore import QSettings, QUrl
        from PySide6.QtWidgets import QApplication

        import desktop_browser.app as appmod
    except ImportError as exc:
        pytest.skip(f"PySide6 unavailable: {exc}")
    import desktop_browser.crash as crash

    monkeypatch.setattr(crash, "crash_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    doc = str(tmp_path / "договор.pdf")

    class Fake:
        def ask_stream(self, query, history=None, **kwargs):
            meta = SimpleNamespace(strategy=SimpleNamespace(name="RAG"), rag_used=True, rag_sources=[doc],
                                   web_used=False, web_sources=[],
                                   citations=[{"n": 1, "file": doc, "label": "договор.pdf, стр. 3"}])
            return meta, iter(["Оплата в течение 10 дней [1]."])

    window = appmod.FluxionWindow(assistant=Fake(), qsettings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    window.prompt.setText("Какой срок оплаты?")
    window.send_message()
    deadline = time.time() + 5
    while window.generating and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    text = window.messages.toPlainText()
    assert "Источники:" in text and "[1] договор.pdf, стр. 3" in text
    opened = []
    monkeypatch.setattr(appmod.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    window._on_anchor(QUrl.fromLocalFile(doc).toString())
    assert opened == [QUrl.fromLocalFile(doc).toLocalFile()]  # the document opens, not the browser
    window.close()
