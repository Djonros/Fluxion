"""«Памятка» (Инструменты → Памятка…): short notes on Fluxion, one Markdown file a topic.

The topics are the files of desktop_browser/notes/: the first «# » line is the
title, the name sets the order («20-autocad.md» comes after «10-…»). A new
topic is a new file; nothing else has to change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QSplitter,
                               QTextBrowser, QVBoxLayout, QWidget)

NOTES_DIR = Path(__file__).with_name("notes")


@dataclass
class Note:
    file: Path
    title: str
    text: str


def load_notes(folder: Path = NOTES_DIR) -> list[Note]:
    notes = []
    for path in sorted(folder.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        notes.append(Note(path, m.group(1).strip() if m else path.stem, text))
    return notes


def matches(note: Note, query: str) -> bool:
    words = query.lower().replace("ё", "е").split()
    text = note.text.lower().replace("ё", "е")
    return all(w in text for w in words)


class NotesDialog(QDialog):
    def __init__(self, parent=None, notes: list[Note] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Памятка Fluxion")
        self.resize(1000, 640)
        self.notes = load_notes() if notes is None else notes
        box = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по памятке…")
        self.search.textChanged.connect(self._filter)
        box.addWidget(self.search)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        left = QWidget()
        lay = QHBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.list)
        split.addWidget(left)
        split.addWidget(self.view)
        split.setSizes([260, 740])
        box.addWidget(split, 1)
        self._shown: list[Note] = []
        self._filter("")

    def _filter(self, query: str) -> None:
        self._shown = [n for n in self.notes if matches(n, query)]
        self.list.clear()
        for n in self._shown:
            self.list.addItem(QListWidgetItem(n.title))
        if self._shown:
            self.list.setCurrentRow(0)
        else:
            self.view.setMarkdown("Ничего не найдено." if self.notes else "Памятка пока пуста.")

    def _show(self, row: int) -> None:
        if 0 <= row < len(self._shown):
            self.view.setMarkdown(self._shown[row].text)
            self.view.setSearchPaths([str(self._shown[row].file.parent)])
