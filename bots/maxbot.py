"""MAX (VK's messenger): the Bot API with long polling — https://dev.max.ru/docs-api.

    GET  /me                                   the bot
    GET  /updates?marker=&timeout=&types=      new events; ``message_created`` carries the message
    POST /messages?user_id=|chat_id=           {"text": "..."} or with attachments
    POST /uploads?type=file                    {"url"}: the file goes there, the answer is its token
         attachments: [{"type": "file", "payload": {"token": "..."}}]

The token is passed in the ``Authorization`` header and, for the older API host, as
``access_token`` too. A bot is created in MAX by @MasterBot; at present only for
organisations verified on the MAX platform for business.

Written by the published API (the TamTam-compatible one MAX keeps); the hosts were not
reachable from the development machine, so it was checked against a fake server only —
«Проверить» in the settings window says at once whether the real one answers.
"""
from __future__ import annotations

import time
from pathlib import Path

from .base import Adapter, Attachment, BotError, Incoming, check, save_download

API = "https://botapi.max.ru"


class Max(Adapter):
    platform = "max"
    MAX_TEXT = 3900  # MAX: 4000

    def __init__(self, token: str, proxy: str = "", session=None, api: str = API):
        super().__init__(token, proxy, session)
        self.api = api.rstrip("/")
        self.marker = None

    def _request(self, verb: str, path: str, wait: float = 30, params: dict | None = None, **kwargs) -> dict:
        params = dict(params or {}, access_token=self.token)
        headers = {"Authorization": self.token}
        try:
            response = self.session.request(verb, f"{self.api}{path}", params=params, headers=headers,
                                            timeout=wait, **kwargs)
        except Exception as exc:
            raise BotError(self.clean(f"MAX недоступен: {exc}")) from exc
        return check(response, "MAX")

    def me(self) -> str:
        bot = self._request("GET", "/me")
        return "@" + str(bot.get("username") or bot.get("name") or "бот")

    def poll(self, timeout: int = 25) -> list[Incoming]:
        params = {"timeout": timeout, "types": "message_created", "limit": 100}
        if self.marker is not None:
            params["marker"] = self.marker
        data = self._request("GET", "/updates", wait=timeout + 10, params=params)
        if data.get("marker") is not None:
            self.marker = data["marker"]
        out = []
        for update in data.get("updates") or []:
            if update.get("update_type") != "message_created":
                continue
            message = update.get("message") or {}
            sender = message.get("sender") or {}
            if not sender or sender.get("is_bot"):
                continue
            recipient = message.get("recipient") or {}
            chat = recipient.get("chat_id") or ""
            body = message.get("body") or {}
            name = sender.get("name") or " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
            item = Incoming(self.platform, str(sender["user_id"]),
                            f"chat:{chat}" if chat and recipient.get("chat_type") not in (None, "dialog")
                            else f"user:{sender['user_id']}", name or str(sender["user_id"]), body.get("text") or "")
            for attachment in body.get("attachments") or []:
                payload = attachment.get("payload") or {}
                if attachment.get("type") == "audio" and payload.get("url"):
                    item.attachments.append(Attachment("voice", payload["url"], "voice.ogg"))
                elif attachment.get("type") == "file" and payload.get("url"):
                    item.attachments.append(Attachment("file", payload["url"], attachment.get("filename") or "file"))
            out.append(item)
        return out

    @staticmethod
    def _target(chat_id: str) -> dict:
        kind, _, value = chat_id.partition(":")
        return {"chat_id": value} if kind == "chat" else {"user_id": value or chat_id}

    def send_text(self, chat_id: str, text: str) -> None:
        for part in self.parts(text):
            self._request("POST", "/messages", params=self._target(chat_id), json={"text": part})

    def send_file(self, chat_id: str, path: Path, caption: str = "") -> None:
        path = Path(path)
        slot = self._request("POST", "/uploads", params={"type": "file"})
        try:
            with path.open("rb") as handle:
                response = self.session.post(slot["url"], files={"data": (path.name, handle)}, timeout=300)
        except Exception as exc:
            raise BotError(self.clean(f"MAX: файл не загружен: {exc}")) from exc
        token = check(response, "MAX").get("token") or slot.get("token")
        if not token:
            raise BotError("MAX: загрузка не вернула токен файла")
        message = {"text": caption[:3900], "attachments": [{"type": "file", "payload": {"token": token}}]}
        for attempt in range(5):  # the file is processed for a moment: «attachment.not.ready»
            try:
                self._request("POST", "/messages", params=self._target(chat_id), json=message)
                return
            except BotError as exc:
                if "not.ready" not in str(exc) or attempt == 4:
                    raise
                time.sleep(1 + attempt)

    def download(self, attachment: Attachment, folder: Path) -> Path:
        try:
            response = self.session.get(attachment.ref, timeout=120)
        except Exception as exc:
            raise BotError(self.clean(f"MAX: файл не скачан: {exc}")) from exc
        if response.status_code >= 400:
            raise BotError(f"MAX: файл не скачан ({response.status_code})")
        return save_download(folder, attachment.name, response.content, "MAX")
