"""The «Инструкция» of the documentation site is the manual of the program
(scripts/mkdocs_manual.py): the same pages, titles and sections."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _hook():
    spec = importlib.util.spec_from_file_location("mkdocs_manual", ROOT / "scripts" / "mkdocs_manual.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _nav():
    class Loader(yaml.SafeLoader):
        pass

    # mkdocs.yml has !!python/name tags (emoji, slugify): read them as plain values
    Loader.add_multi_constructor("tag:yaml.org,2002:python/", lambda loader, suffix, node: None)
    config = yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=Loader)
    assert "scripts/mkdocs_manual.py" in config["hooks"]
    return config["nav"]


def test_the_site_has_every_page_of_the_program_manual():
    from desktop_browser.manual import MANUAL_DIR, load_pages

    hook = _hook()
    pages = hook.read_pages()
    program = load_pages(MANUAL_DIR)
    assert [(p["key"], p["title"], p["section"], p["pro"]) for p in pages] == [
        (p.key, p.title, p.section, p.pro) for p in program
    ]
    nav = hook.manual_nav(pages)
    listed = [path for section in nav for items in section.values() for item in items for path in item.values()]
    assert listed == [f"instrukciya/{p.key}.md" for p in program]
    assert [next(iter(s)) for s in nav][:3] == ["Начало работы", "Чат и документы", "КОМПАС-3D"]


def test_a_pro_page_says_so_and_the_text_is_the_program_text():
    hook = _hook()
    pages = {p["key"]: p for p in hook.read_pages()}
    archive = hook.page_markdown(pages["arhiv-kd"])
    assert archive.startswith("# Архив КД\n") and "Fluxion Pro" in archive
    assert pages["arhiv-kd"]["body"] in archive and "<!--" not in archive
    assert "Fluxion Pro" not in hook.page_markdown(pages["chat"])


def test_the_nav_has_the_manual_placeholder_and_its_files_exist():
    nav = _nav()
    titles = [next(iter(entry)) for entry in nav]
    assert "Инструкция" in titles and titles[-1] == "Для разработчиков"
    hook = _hook()
    config = {"nav": nav}
    hook.on_config(config)
    assert next(e for e in config["nav"] if "Инструкция" in e)["Инструкция"]

    def paths(entries):
        for entry in entries:
            for value in entry.values():
                if isinstance(value, list):
                    yield from paths(value)
                elif not value.startswith(("http", "instrukciya/")):
                    yield value

    missing = [p for p in paths(nav) if not (ROOT / "docs" / p).is_file()]
    assert not missing, missing
