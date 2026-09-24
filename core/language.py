import re

_CODE_RE = re.compile(r"```.*?```|`[^`\n]+`", re.S)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")

DIRECTIVES = {
    "ru": ("по-русски", "на русском", "по русски", "русском"),
    "en": ("in english", "english", "по-английски", "на английском"),
}

LANG_NAMES = {"ru": "русском", "en": "английском"}

_MIN_TOTAL = 40
_MIN_LETTERS = 4
_RATIO = 0.6


def _natural_text(text):
    text = _URL_RE.sub(" ", text or "")
    text = _CODE_RE.sub(" ", text)
    return text


def detect_language(text):
    natural = _natural_text(text)
    cyr = sum(1 for ch in natural if "\u0400" <= ch <= "\u04FF")
    lat = sum(1 for ch in natural if ch.isascii() and ch.isalpha())
    total = cyr + lat
    if total < _MIN_LETTERS:
        return None
    if total < _MIN_TOTAL:
        if cyr and not lat:
            return "ru"
        if lat and not cyr:
            return "en"
        return None
    if cyr / total >= _RATIO:
        return "ru"
    if lat / total >= _RATIO:
        return "en"
    return None


def resolve_target(setting, user_text):
    setting = (setting or "auto").strip().lower()
    if setting in DIRECTIVES:
        return setting
    lowered = (user_text or "").lower()
    for lang, phrases in DIRECTIVES.items():
        if any(phrase in lowered for phrase in phrases):
            return lang
    return detect_language(user_text)


def matches_language(text, target):
    if not target:
        return True
    detected = detect_language(text)
    if detected is None:
        return True
    return detected == target
