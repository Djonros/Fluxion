"""Коннектор «Активность»: в какой программе и над каким документом вы работали, и сколько.

Every ``TICK`` seconds Fluxion asks Windows which window is in front (its program and
title) and how long the keyboard and mouse were untouched. Time in one window is joined
into an interval — «КОМПАС-3D — 792720.0000010.a3d, 10:05–11:40» — written when the
window changes, after ``IDLE`` of no input (away from the computer) or every ``SPLIT``.

    settings = {"titles": true,                         # false — only the program, no titles
                "exclude": "keepass.exe;bitwarden.exe"}  # these programs are not written at all

Never: keys pressed, screenshots, the text of windows, the addresses of a browser.
Private windows of browsers (InPrivate, инкогнито) are not written.
"""
from __future__ import annotations

import os
import sys
import threading
import time

from .connector import Connector, Event

TICK = 5.0
IDLE = 5 * 60.0
SPLIT = 15 * 60.0
SHORTEST = 10.0
DEFAULT_EXCLUDE = ("keepass.exe;keepassxc.exe;1password.exe;bitwarden.exe;enpass.exe;"
                   "lockapp.exe;logonui.exe")
PRIVATE_MARKS = ("inprivate", "инкогнито", "incognito", "private browsing", "приватный просмотр",
                 "приватное окно")
NAMES = {  # programs the user knows by another name than the exe
    "kompas.exe": "КОМПАС-3D", "acad.exe": "AutoCAD", "winword.exe": "Word", "excel.exe": "Excel",
    "outlook.exe": "Outlook", "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox",
    "browser.exe": "Яндекс Браузер", "telegram.exe": "Telegram", "zoom.exe": "Zoom",
    "explorer.exe": "Проводник", "1cv8.exe": "1С", "1cv8c.exe": "1С", "fluxion.exe": "Fluxion",
    "totalcmd64.exe": "Total Commander", "acrord32.exe": "Acrobat Reader", "acrobat.exe": "Acrobat",
    "teams.exe": "Teams", "ms-teams.exe": "Teams", "yandex-telemost.exe": "Телемост",
}


def app_name(exe: str) -> str:
    base = os.path.basename(exe).lower()
    return NAMES.get(base) or os.path.splitext(os.path.basename(exe))[0] or "?"


def _windows_probe():
    """(exe path, window title, seconds without input) of the window in front, or None."""
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    idle = 0.0
    if user32.GetLastInputInfo(ctypes.byref(info)):
        idle = ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            size = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                exe = path.value
        finally:
            kernel32.CloseHandle(handle)
    return exe, buf.value, idle


class ActivityConnector(Connector):
    id = "activity"
    title = "Активность"
    description = "В какой программе и над каким документом вы работали и сколько: «что я делал вчера», часы по изделию."
    reads = ("какое окно впереди: программа и заголовок окна", "сколько времени не трогали мышь и клавиатуру")
    records = ("программа, заголовок окна, начало и длительность",)
    never = ("не записывает нажатия клавиш", "не делает снимков экрана",
             "не читает текст окон и адреса в браузере", "не пишет приватные окна браузера")

    def __init__(self, settings: dict | None = None, probe=None):
        super().__init__(settings)
        self.titles = self.settings.get("titles", True) is not False
        text = self.settings.get("exclude", DEFAULT_EXCLUDE)
        self.exclude = {m.strip().lower() for m in str(text).replace(",", ";").split(";") if m.strip()}
        self.custom_probe = probe is not None
        self.probe = probe or (_windows_probe if sys.platform == "win32" else (lambda: None))
        self.current: list | None = None   # [exe, title, start, last seen]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def skipped(self, exe: str, title: str) -> bool:
        if os.path.basename(exe).lower() in self.exclude:
            return True
        low = title.lower()
        return any(mark in low for mark in PRIVATE_MARKS)

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        try:
            seen = self.probe()
        except Exception:
            seen = None
        if seen is None:
            self.close(now)
            return
        exe, title, idle = seen
        if idle >= IDLE:
            self.close(min(now, now - idle + TICK))
            return
        if not self.titles:
            title = ""
        if self.skipped(exe, title):
            self.close(now)
            return
        cur = self.current
        if cur is not None and cur[0] == exe and cur[1] == title:
            cur[3] = now
            if now - cur[2] >= SPLIT:
                self.close(now)
                self.current = [exe, title, now, now]
            return
        self.close(now)
        self.current = [exe, title, now, now]

    def close(self, now: float) -> Event | None:
        cur, self.current = self.current, None
        if cur is None:
            return None
        exe, title, start, last = cur
        end = max(last, min(now, last + TICK))
        if end - start < SHORTEST:
            return None
        name = app_name(exe)
        event = Event(self.id, "app.focus", f"{name} — {title}" if title else name, ref=os.path.basename(exe),
                      detail=exe, time=start, duration=round(end - start))
        self.emit(event)
        return event

    def start(self, emit) -> None:
        super().start(emit)
        if sys.platform != "win32" and not self.custom_probe:
            self.fail("работает только в Windows")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fluxion-assistant-activity")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(TICK):
            self.tick()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(TICK + 2)
            self._thread = None
        self.close(time.time())
        super().stop()
