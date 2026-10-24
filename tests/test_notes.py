"""«Памятка» (desktop_browser/notes.py): the topics are the Markdown files of desktop_browser/notes."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from desktop_browser import notes  # noqa: E402


def test_shipped_notes_have_titles():
    loaded = notes.load_notes()
    assert loaded, "desktop_browser/notes is empty"
    assert all(n.title and n.text.strip() for n in loaded)
    assert any("AutoCAD" in n.title for n in loaded)


def test_order_title_and_search(tmp_path):
    (tmp_path / "20-b.md").write_text("# Второй\nпро КОМПАС", encoding="utf-8")
    (tmp_path / "10-a.md").write_text("без заголовка, про ёлку", encoding="utf-8")
    loaded = notes.load_notes(tmp_path)
    assert [n.title for n in loaded] == ["10-a", "Второй"]
    assert notes.matches(loaded[1], "компас")
    assert notes.matches(loaded[0], "елку")
    assert not notes.matches(loaded[0], "компас")
