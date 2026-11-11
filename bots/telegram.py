"""Telegram: the Bot API with long polling (getUpdates) — https://core.telegram.org/bots/api.

The token comes from @BotFather. In Russia api.telegram.org may need a proxy
(«http://host:port» in the settings).
"""
from __future__ import annotations

from pathlib import Path

from .base import Adapter, Attachment, BotError, Incoming, check, save_download

API = "https://api.telegram.org"


class Telegram(Adapter):
    platform = "telegram"
    MAX_TEXT = 4000  # Telegram: 4096

    def __init__(self, token: str, proxy: str = "", session=None):
        super().__init__(token, proxy, session)
        self.offset = 0

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    def _call(self, method: str, wait: float = 30, **params) -> dict:
        try:
            response = self.session.post(self._url(method), data=params, timeout=wait)
        except Exception as exc:
            raise BotError(self.clean(f"Telegram недоступен: {exc}")) from exc
        return check(response, "Telegram")

    def me(self) -> str:
        bot = self._call("getMe")["result"]
        return "@" + str(bot.get("username") or bot.get("first_name") or "бот")

    def poll(self, timeout: int = 25) -> list[Incoming]:
        data = self._call("getUpdates", wait=timeout + 10, offset=self.offset, timeout=timeout,
                          allowed_updates='["message"]')
        out = []
        for update in data.get("result") or []:
            self.offset = max(self.offset, int(update["update_id"]) + 1)
            message = update.get("message") or {}
            sender = message.get("from") or {}
            if not sender or sender.get("is_bot"):
                continue
            name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or \
                str(sender.get("username") or sender["id"])
            item = Incoming(self.platform, str(sender["id"]), str(message["chat"]["id"]), name,
                            message.get("text") or message.get("caption") or "")
            voice = message.get("voice") or message.get("audio")
            if voice:
                item.attachments.append(Attachment("voice", voice["file_id"], "voice.ogg"))
            document = message.get("document")
            if document:
                item.attachments.append(Attachment("file", document["file_id"], document.get("file_name") or "file"))
            out.append(item)
        return out

    def send_text(self, chat_id: str, text: str) -> None:
        for part in self.parts(text):
            self._call("sendMessage", chat_id=chat_id, text=part, disable_web_page_preview="true")

    def send_file(self, chat_id: str, path: Path, caption: str = "") -> None:
        path = Path(path)
        try:
            with path.open("rb") as handle:
                response = self.session.post(self._url("sendDocument"), data={"chat_id": chat_id,
                                                                              "caption": caption[:1000]},
                                             files={"document": (path.name, handle)}, timeout=300)
        except BotError:
            raise
        except Exception as exc:
            raise BotError(self.clean(f"Telegram: файл не отправлен: {exc}")) from exc
        check(response, "Telegram")

    def download(self, attachment: Attachment, folder: Path) -> Path:
        info = self._call("getFile", file_id=attachment.ref)["result"]
        try:
            response = self.session.get(f"{API}/file/bot{self.token}/{info['file_path']}", timeout=120)
        except Exception as exc:
            raise BotError(self.clean(f"Telegram: файл не скачан: {exc}")) from exc
        if response.status_code >= 400:
            raise BotError(f"Telegram: файл не скачан ({response.status_code})")
        return save_download(folder, attachment.name or Path(info["file_path"]).name, response.content, "Telegram")
