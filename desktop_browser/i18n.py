"""Language of the interface (меню «Вид» → «Язык / Language»).

The text of the window is written in Russian right in the code, wrapped in
``tr``: ``tr("Закрыть")``, ``tr("Отчёт: {name}", name=path.name)``. The Russian
text is the key; ``locales/<code>.json`` maps it to the text of another
language. A text without a translation stays Russian, so a new string never
breaks the window. A new language is one more file in locales/ and a line in
LANGUAGES.

The language is read once at start (``load_saved_language``), before the
window is built; a change applies after a restart of the program.
"""
from __future__ import annotations

import json
from pathlib import Path

LANGUAGES = {
    "ru": "Русский",
    "en": "English",
    "uk": "Українська",
    "be": "Беларуская",
    "kk": "Қазақша",
    "es": "Español",
    "fr": "Français",
}
DEFAULT_LANGUAGE = "ru"
SETTING_KEY = "ui_language"
LOCALES_DIR = Path(__file__).with_name("locales")

_language = DEFAULT_LANGUAGE
_catalog: dict[str, str] = {}


def language() -> str:
    """The code of the interface language now in use ("ru", "en")."""
    return _language


def load_catalog(code: str, folder: Path = LOCALES_DIR) -> dict[str, str]:
    """Translations of *code*: Russian text → text; {} for Russian or a broken file."""
    if code == DEFAULT_LANGUAGE:
        return {}
    try:
        data = json.loads((folder / f"{code}.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if v} if isinstance(data, dict) else {}


def set_language(code: str) -> str:
    """Use *code* from now on (an unknown code means Russian); returns the code in use."""
    global _language, _catalog
    code = code if code in LANGUAGES else DEFAULT_LANGUAGE
    _language, _catalog = code, load_catalog(code)
    return code


def saved_language(qsettings) -> str:
    value = str(qsettings.value(SETTING_KEY, DEFAULT_LANGUAGE) or "")
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


def load_saved_language() -> str:
    """Switch to the language chosen in the menu (QSettings «Fluxion»)."""
    from PySide6.QtCore import QSettings

    return set_language(saved_language(QSettings("Fluxion", "Fluxion")))


def tr(text: str, **values) -> str:
    """*text* in the interface language; ``{name}`` fields are filled from *values*."""
    text = _catalog.get(text, text)
    return text.format(**values) if values else text
