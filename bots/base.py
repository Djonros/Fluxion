"""What every messenger adapter gives and takes."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_DOWNLOAD = 50 * 1024 * 1024  # a file sent to the bot: bigger is not saved
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def proxy_url(text: str) -> str:
    """The proxy as httpx wants it: «адрес:порт» means HTTP, «socks5://логин:пароль@адрес:порт» works too."""
    text = (text or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if lower.startswith("socks://"):
        return "socks5://" + text[len("socks://"):]
    if "://" not in text:
        return "http://" + text
    return text


class BotError(RuntimeError):
    """The messenger refused (a wrong token, no network): said in words for the settings window."""


@dataclass
class Attachment:
    kind: str  # "voice", "file", "photo"
    ref: str  # the id or URL the adapter downloads it by
    name: str = ""


@dataclass
class Incoming:
    platform: str  # "telegram", "max", "vk"
    user_id: str
    chat_id: str  # where to answer
    name: str  # the user's name, for the allow-list
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)


class Adapter:
    """A messenger: ``poll`` waits for new messages, ``send_text`` and ``send_file`` answer."""

    platform = ""
    MAX_TEXT = 4000  # a longer answer goes in parts

    def __init__(self, token: str, proxy: str = "", session=None):
        if not token:
            raise BotError("нет токена бота")
        self.token = token
        self.proxy = proxy
        if session is None:
            import httpx

            proxy = proxy_url(proxy)
            if proxy.startswith("socks"):
                try:
                    import socksio  # noqa: F401 — httpx needs it for a SOCKS proxy
                except ImportError:
                    raise BotError("SOCKS-прокси не поддерживается: укажите HTTP-прокси, http://адрес:порт") from None
            try:
                session = httpx.Client(proxy=proxy or None, follow_redirects=True)
            except ValueError as exc:  # an unknown scheme
                raise BotError(f"прокси не понят: {exc}. Пример: socks5://адрес:порт или http://адрес:порт") from None
        self.session = session

    def clean(self, text: str) -> str:
        """An error text without the token (a URL of Telegram has it in its path)."""
        return str(text).replace(self.token, "***") if self.token else str(text)

    def me(self) -> str:
        """The name of the bot: a check that the token works."""
        raise NotImplementedError

    def poll(self, timeout: int = 25) -> list[Incoming]:
        raise NotImplementedError

    def send_text(self, chat_id: str, text: str) -> None:
        raise NotImplementedError

    def send_file(self, chat_id: str, path: Path, caption: str = "") -> None:
        raise NotImplementedError

    def download(self, attachment: Attachment, folder: Path) -> Path:
        raise NotImplementedError

    @classmethod
    def parts(cls, text: str) -> list[str]:
        """A long answer cut at paragraphs, then lines, under the messenger's limit."""
        text = text.strip() or "…"
        out = []
        while len(text) > cls.MAX_TEXT:
            cut = max(text.rfind("\n\n", 0, cls.MAX_TEXT), text.rfind("\n", 0, cls.MAX_TEXT))
            cut = cut if cut > cls.MAX_TEXT // 2 else cls.MAX_TEXT
            out.append(text[:cut].rstrip())
            text = text[cut:].lstrip()
        out.append(text)
        return out


def check(response, what: str) -> dict:
    """The JSON of a response, or BotError with what the messenger said."""
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or (isinstance(data, dict) and data.get("ok") is False) or \
            (isinstance(data, dict) and "error" in data and isinstance(data["error"], (dict, str))):
        detail = ""
        if isinstance(data, dict):
            error = data.get("error")
            detail = data.get("description") or (error.get("error_msg") if isinstance(error, dict) else error) or \
                data.get("message") or ""
        if response.status_code in (401, 403) or "unauthorized" in str(detail).lower() or \
                "invalid" in str(detail).lower() and "token" in str(detail).lower():
            raise BotError(f"{what}: токен не принят ({detail or response.status_code})")
        raise BotError(f"{what}: {detail or f'ответ {response.status_code}'}")
    return data


def safe_name(name: str, fallback: str = "file") -> str:
    """A file name from a messenger fit for Windows: no folders, no forbidden characters, not too long."""
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = _BAD_CHARS.sub("_", name).strip(" .")
    if not name or name.split(".")[0].upper() in _RESERVED:
        name = f"{fallback}_{name}" if name else fallback
    if len(name) > 150:
        stem, dot, suffix = name.rpartition(".")
        name = (stem[:140] + dot + suffix[:9]) if dot and len(suffix) <= 9 else name[:150]
    return name


def save_download(folder: Path, name: str, content: bytes, what: str, fallback: str = "file") -> Path:
    """Save a downloaded file in *folder* under a safe name; an existing file is kept, the new one gets «(2)»."""
    if len(content) > MAX_DOWNLOAD:
        raise BotError(f"{what}: файл больше {MAX_DOWNLOAD // (1024 * 1024)} МБ — не сохранён")
    folder = Path(folder).resolve()
    target = folder / safe_name(name, fallback)
    stem, suffix = target.stem, target.suffix
    number = 2
    while target.exists():
        target = folder / f"{stem} ({number}){suffix}"
        number += 1
    if target.resolve().parent != folder:
        raise BotError(f"{what}: недопустимое имя файла")
    target.write_bytes(content)
    return target
