"""Reading answers aloud with the Windows voices (SAPI 5, through pywin32).

Speech is asynchronous: speak() returns at once and the voice talks while the
window keeps working; stop() cuts it off. A Russian voice is picked when the
system has one. Code is not read aloud: a code block becomes a short remark.
"""
from __future__ import annotations

import re
import sys

CODE_SKIPPED = "Пример кода пропущен."
_SVSF_ASYNC = 1
_SVSF_PURGE = 2
_SAPI_DONE = 1  # SpeechRunState.SRSEDone

_FENCE_RE = re.compile(r"```.*?(?:```|$)", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)
_QUOTE_RE = re.compile(r"^\s*>\s?", re.M)
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{3,}.*$", re.M)
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_SAVED_IN_RE = re.compile(r"\s+(?:в|во)\s+[A-Za-z]:\\[^\n]*")  # "сохранена в C:\...\x.m3d"
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\n]*")
_ZEROS_RE = re.compile(r"(?<=\d)\.0+\b")  # 100.00 -> 100
_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")  # 96.15 -> 96,15: a Russian voice reads the comma
_TIMES_RE = re.compile(r"(?<=\d)\s*[×xх]\s*(?=\d)")  # 100 × 96 -> 100 на 96


def speech_text(markdown: str) -> str:
    """Plain text of a chat answer for reading aloud."""
    text = _FENCE_RE.sub(f"\n{CODE_SKIPPED}\n", markdown or "")
    text = _INLINE_CODE_RE.sub(lambda m: m.group(1) if len(m.group(1)) <= 30 else "", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub("ссылка", text)
    text = _TABLE_RULE_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = _QUOTE_RE.sub("", text)
    text = _LIST_RE.sub("", text)
    for _ in range(2):  # nested **_bold italic_**
        text = _EMPHASIS_RE.sub(r"\2", text)
    text = text.replace("|", ", ")
    text = _SAVED_IN_RE.sub("", text)
    text = _PATH_RE.sub("", text)
    text = _ZEROS_RE.sub("", text)
    text = _DECIMAL_RE.sub(",", text)
    text = _TIMES_RE.sub(" на ", text)
    lines = [line.strip() for line in text.splitlines() if not line.strip().endswith(":")]
    out: list[str] = []
    for line in lines:
        if not line:
            continue
        if line == CODE_SKIPPED and out and out[-1] == CODE_SKIPPED:
            continue  # several code blocks in a row: say it once
        if out and out[-1][-1:] not in ".!?:;…":
            out[-1] += "."
        out.append(line)
    return " ".join(out).strip()


class Speaker:
    """One SAPI voice for the window. All calls must come from the GUI thread."""

    def __init__(self):
        self._voice = None
        self.error = ""

    def available(self) -> bool:
        return self._ensure() is not None

    def _ensure(self):
        if self._voice is not None:
            return self._voice
        if sys.platform != "win32":
            self.error = "Озвучка работает только в Windows."
            return None
        try:
            import win32com.client

            voice = win32com.client.Dispatch("SAPI.SpVoice")
            for token in voice.GetVoices():
                if token.GetAttribute("Language").split(";")[0].lower() == "419":  # Russian
                    voice.Voice = token
                    break
        except Exception as exc:
            self.error = f"Голос Windows недоступен: {exc}"
            return None
        self._voice = voice
        return voice

    def voice_name(self) -> str:
        voice = self._ensure()
        try:
            return voice.Voice.GetDescription() if voice is not None else ""
        except Exception:
            return ""

    def speak(self, markdown: str) -> bool:
        """Start reading *markdown* aloud, cutting off what is being read."""
        text = speech_text(markdown)
        voice = self._ensure()
        if voice is None or not text:
            return False
        try:
            voice.Speak(text, _SVSF_ASYNC | _SVSF_PURGE)
        except Exception as exc:
            self.error = f"Не удалось озвучить ответ: {exc}"
            return False
        return True

    def speaking(self) -> bool:
        if self._voice is None:
            return False
        try:
            return self._voice.Status.RunningState != _SAPI_DONE
        except Exception:
            return False

    def stop(self) -> None:
        if self._voice is None:
            return
        try:
            self._voice.Speak("", _SVSF_ASYNC | _SVSF_PURGE)
        except Exception:
            pass
