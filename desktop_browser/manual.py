"""«Инструкция по инструментам» (Помощь → Инструкция, F1): one page a tool.

The pages are the Markdown files of desktop_browser/manual/. A page starts with
its title («# …») and a line of what it is about:

    <!-- раздел: КОМПАС-3D | инструмент: assembly_report | pro -->

the section of the contents, the tool the page opens («Открыть инструмент»,
the keys of FluxionWindow.manual_tools) and «pro» for a feature of Fluxion Pro.
The file name sets the order. Every page has the same parts: «Для чего», «Как
запустить», «Пошагово», «Что получится», «Если не получается».

A window of a tool gets a «?» button (``help_button``) and F1 that open the
manual on its page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

MANUAL_DIR = Path(__file__).with_name("manual")
_META_RE = re.compile(r"^<!--(.*?)-->\s*$", re.MULTILINE)


@dataclass
class Page:
    key: str  # "otchet-po-sborke": the file name without its order number
    title: str
    section: str
    tool: str  # "" — nothing to open
    pro: bool
    text: str  # Markdown without the title and the meta line


def load_pages(folder: Path = MANUAL_DIR) -> list[Page]:
    pages = []
    for path in sorted(folder.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        title = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
        meta = _META_RE.search(raw)
        fields = {}
        flags = set()
        for part in (meta.group(1).split("|") if meta else []):
            name, _, value = part.partition(":")
            if value:
                fields[name.strip().lower()] = value.strip()
            else:
                flags.add(name.strip().lower())
        body = raw
        if title:
            body = body.replace(title.group(0), "", 1)
        if meta:
            body = body.replace(meta.group(0), "", 1)
        tool = fields.get("инструмент", "")
        pages.append(Page(re.sub(r"^\d+-", "", path.stem), title.group(1).strip() if title else path.stem,
                          fields.get("раздел", "Разное"), "" if tool == "-" else tool, "pro" in flags,
                          body.strip()))
    return pages


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е")


def matches(page: Page, query: str) -> bool:
    """Does the page have every word of *query* (case and ё ignored)?"""
    haystack = _fold(f"{page.title} {page.section} {page.text}")
    return all(word in haystack for word in _fold(query).split())


def page_markdown(page: Page) -> str:
    badge = "**Pro** — функция Fluxion Pro (меню «Помощь» → «Лицензия…»)" if page.pro else "Бесплатно"
    return f"# {page.title}\n\n{badge}\n\n{page.text}"


class ManualDialog(QDialog):
    """Contents on the left (sections and pages), search on top, the page on the right
    and «Открыть инструмент» when the page has a tool and *tools* can open it."""

    def __init__(self, parent=None, page: str = "", tools: dict[str, Callable[[], object]] | None = None,
                 pages: list[Page] | None = None):
        super().__init__(parent)
        self.pages = load_pages() if pages is None else pages
        self.tools = tools or {}
        self.current: Page | None = None
        self.setWindowTitle("Инструкция по инструментам")
        self.resize(980, 640)
        box = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по инструкции: «материал», «DXF», «архив»…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        box.addWidget(self.search)
        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._picked)
        split.addWidget(self.tree)
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(QDesktopServices.openUrl)
        right_box.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.hint = QLabel()
        self.hint.setObjectName("subtitle")
        row.addWidget(self.hint, 1)
        self.open_button = QPushButton("Открыть инструмент")
        self.open_button.setObjectName("sendButton")
        self.open_button.clicked.connect(self._open_tool)
        row.addWidget(self.open_button)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        right_box.addLayout(row)
        split.addWidget(right)
        split.setSizes([340, 700])
        box.addWidget(split, 1)
        self._fill("")
        self.show_page(page)

    def _fill(self, query: str) -> None:
        self.tree.clear()
        sections: dict[str, QTreeWidgetItem] = {}
        first = None
        for page in self.pages:
            if query.strip() and not matches(page, query):
                continue
            parent = sections.get(page.section)
            if parent is None:
                parent = sections[page.section] = QTreeWidgetItem(self.tree, [page.section])
                parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
                parent.setExpanded(True)
            item = QTreeWidgetItem(parent, [page.title + ("  · Pro" if page.pro else "")])
            item.setData(0, Qt.UserRole, page.key)
            first = first or item
        if first is None:
            self.current = None
            self.view.setMarkdown("Ничего не найдено." if self.pages else "Инструкция пуста.")
            self.open_button.setEnabled(False)
            self.hint.clear()
        elif query.strip():
            self.tree.setCurrentItem(first)

    def show_page(self, key: str) -> None:
        """Select the page *key* (the first page when there is no such page)."""
        items = [self.tree.topLevelItem(i).child(j) for i in range(self.tree.topLevelItemCount())
                 for j in range(self.tree.topLevelItem(i).childCount())]
        target = next((it for it in items if it.data(0, Qt.UserRole) == key), items[0] if items else None)
        if target is not None:
            self.tree.setCurrentItem(target)

    def _picked(self, item, _previous=None) -> None:
        key = item.data(0, Qt.UserRole) if item is not None else None
        page = next((p for p in self.pages if p.key == key), None)
        if page is None:
            return
        self.current = page
        self.view.setMarkdown(page_markdown(page))
        can_open = bool(page.tool) and page.tool in self.tools
        self.open_button.setVisible(bool(page.tool))
        self.open_button.setEnabled(can_open)
        self.hint.setText("" if can_open or not page.tool else "Закройте окно инструмента, чтобы открыть его отсюда")

    def _open_tool(self) -> None:
        if self.current is None or self.current.tool not in self.tools:
            return
        tool = self.tools[self.current.tool]
        self.accept()  # the manual closes, the tool opens
        tool()


def open_manual(parent=None, page: str = "", tools: dict[str, Callable[[], object]] | None = None) -> None:
    ManualDialog(parent, page, tools).exec()


def help_button(dialog, page: str) -> QPushButton:
    """A «?» button for the window of a tool: the manual on its page; F1 in the window too."""
    button = QPushButton("?")
    button.setToolTip("Инструкция (F1)")
    button.setFixedWidth(32)
    button.clicked.connect(lambda: open_manual(dialog, page))
    QShortcut(QKeySequence("F1"), dialog, activated=lambda: open_manual(dialog, page))
    return button
