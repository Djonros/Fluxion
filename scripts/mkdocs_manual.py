"""mkdocs hook: the «Инструкция» of the documentation site is the manual of the
program (desktop_browser/manual/*.md, Помощь → «Инструкция», F1).

The pages are not copied into docs/: they are added while the site is built,
so the site always says what the program says. The section of a page (its
``<!-- раздел: … -->`` line) is its group in the menu; «pro» marks a page of a
Fluxion Pro feature. «Открыть инструмент» exists only in the program, so the
site tells where the tool is instead.

The parsing repeats desktop_browser.manual._read_pages without Qt: the Pages
workflow installs only mkdocs-material.
"""
from __future__ import annotations

import re
from pathlib import Path

MANUAL_DIR = Path(__file__).resolve().parent.parent / "desktop_browser" / "manual"
SECTION_DIR = "instrukciya"
NAV_TITLE = "Инструкция"
_META_RE = re.compile(r"^<!--(.*?)-->\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

PRO_NOTE = (
    '!!! note "Fluxion Pro"\n'
    "    Эта возможность входит в Fluxion Pro — см. [Лицензия Pro](../guides/licensing.md).\n"
)
FOOTER = (
    "\n\n---\n\n"
    "*Эта страница — из «Инструкции» в программе: меню **Помощь → «Инструкция»** или **F1**. "
    "Там же кнопка «Открыть инструмент».*\n"
)


def read_pages(folder: Path = MANUAL_DIR) -> list[dict]:
    """The Russian pages of the manual in their order: key, title, section, pro, body."""
    pages = []
    for path in sorted(folder.glob("*.md")):
        raw = path.read_text(encoding="utf-8-sig")
        title = _TITLE_RE.search(raw)
        meta = _META_RE.search(raw)
        fields, flags = {}, set()
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
        pages.append({
            "key": re.sub(r"^\d+-", "", path.stem),
            "title": title.group(1).strip() if title else path.stem,
            "section": fields.get("раздел", "Разное"),
            "pro": "pro" in flags,
            "body": body.strip(),
        })
    return pages


def page_path(page: dict) -> str:
    return f"{SECTION_DIR}/{page['key']}.md"


def page_markdown(page: dict) -> str:
    parts = [f"# {page['title']}\n"]
    if page["pro"]:
        parts.append(PRO_NOTE)
    parts.append(page["body"])
    return "\n".join(parts) + FOOTER


def manual_nav(pages: list[dict]) -> list[dict]:
    """[{section: [{title: path}, …]}, …] in the order of the manual."""
    sections: dict[str, list] = {}
    for page in pages:
        sections.setdefault(page["section"], []).append({page["title"]: page_path(page)})
    return [{section: items} for section, items in sections.items()]


def on_config(config):
    """Fill the «Инструкция» entry of the nav (``- Инструкция: []`` in mkdocs.yml)."""
    nav = config.get("nav") or []
    for entry in nav:
        if isinstance(entry, dict) and NAV_TITLE in entry:
            entry[NAV_TITLE] = manual_nav(read_pages())
    return config


def on_files(files, config):
    from mkdocs.structure.files import File

    for page in read_pages():
        files.append(File.generated(config, page_path(page), content=page_markdown(page)))
    return files
